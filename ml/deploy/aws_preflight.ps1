# AWS 사전 점검 (읽기 전용). 아무것도 만들거나 지우지 않는다. 자격 증명은 다루지 않고, 사용자의 AWS CLI 설정을 그대로 쓴다.
#
#   powershell -ExecutionPolicy Bypass -File .\deploy\aws_preflight.ps1
#   powershell -ExecutionPolicy Bypass -File .\deploy\aws_preflight.ps1 -Bucket <버킷> -Instance ml.m5.large -Region <리전>
#
# 확인 항목: CLI 설치, 로그인한 계정/사용자, 리전, SageMaker 접근, SageMaker 가 맡을 수 있는 IAM 역할 목록,
#            엔드포인트용 인스턴스 할당량, (선택) S3 버킷 접근과 퍼블릭 차단 설정.
# 권한이 없어 읽지 못하는 항목은 '확인 못 함'으로 표시한다. 그 경우 콘솔에서 직접 보거나 권한을 받아야 한다.
param(
  [string]$Bucket = "",
  [string]$Instance = "ml.m5.large",
  [string]$Region = ""
)
$ErrorActionPreference = "Continue"
$ok = @(); $warn = @(); $unk = @()

function Aws-Json($argv) {
  # aws 명령 자체가 없으면 PowerShell 은 종료 코드를 바꾸지 않아 $LASTEXITCODE 만으로는 성공으로 오인한다
  if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { return @{ ok = $false; text = "aws 명령을 찾을 수 없습니다 (AWS CLI 미설치 또는 PATH 에 없음)" } }
  $global:LASTEXITCODE = 0
  $out = & aws @argv 2>&1
  if ($LASTEXITCODE -ne 0) { return @{ ok = $false; text = ($out | Out-String).Trim() } }
  return @{ ok = $true; text = ($out | Out-String).Trim() }
}
function Section($t) { Write-Host ""; Write-Host "=== $t ===" }

Section "AWS CLI"
$v = Aws-Json @("--version")
if (-not $v.ok) { Write-Host "AWS CLI 를 찾지 못했습니다. 설치 후 다시 실행하세요."; exit 1 }
Write-Host $v.text
$ok += "AWS CLI 설치됨"

$base = @()
if ($Region -ne "") { $base += @("--region", $Region) }

Section "로그인한 주체 (aws sts get-caller-identity)"
$id = Aws-Json (@("sts", "get-caller-identity", "--output", "json") + $base)
if ($id.ok) {
  $j = $id.text | ConvertFrom-Json
  Write-Host "계정: $($j.Account)"
  Write-Host "주체: $($j.Arn)"
  $ok += "AWS 로그인 확인됨 (계정 $($j.Account))"
} else {
  Write-Host $id.text
  Write-Host "로그인이 안 되어 있습니다. aws configure 또는 aws sso login 으로 직접 로그인한 뒤 다시 실행하세요."
  exit 1
}

Section "리전"
$rg = if ($Region -ne "") { $Region } else { (& aws configure get region 2>$null) }
$rg = "$rg".Trim()
if ([string]::IsNullOrWhiteSpace($rg)) { Write-Host "리전이 설정되어 있지 않습니다. -Region 으로 주거나 aws configure 로 설정하세요."; $warn += "리전 미설정" }
else { Write-Host "리전: $rg"; $ok += "리전 $rg" }

Section "SageMaker 접근 (엔드포인트 목록 조회)"
$sm = Aws-Json (@("sagemaker", "list-endpoints", "--max-results", "10", "--query", "Endpoints[].[EndpointName,EndpointStatus]", "--output", "text") + $base)
if ($sm.ok) {
  if ($sm.text -eq "") { Write-Host "기존 엔드포인트 없음" } else { Write-Host $sm.text }
  $ok += "SageMaker 조회 가능"
} else { Write-Host $sm.text; $unk += "SageMaker 조회 (권한 없음 또는 리전 문제)" }

Section "SageMaker 가 맡을 수 있는 IAM 역할"
$ro = Aws-Json @("iam", "list-roles", "--query", "Roles[?contains(to_string(AssumeRolePolicyDocument), 'sagemaker.amazonaws.com')].[RoleName,Arn]", "--output", "text")
if ($ro.ok) {
  if ($ro.text -eq "") { Write-Host "SageMaker 를 신뢰하는 역할이 없습니다. 콘솔에서 SageMaker 실행 역할을 만들어야 합니다 (S3 읽기 권한 포함)."; $warn += "SageMaker 실행 역할 없음" }
  else { Write-Host $ro.text; $ok += "SageMaker 실행 역할 있음 (위 목록의 ARN 을 sm_deploy.py create --role 에 사용)" }
} else { Write-Host $ro.text; $unk += "IAM 역할 목록 (iam:ListRoles 권한 없음 가능). 콘솔 IAM > 역할에서 직접 확인" }

Section "엔드포인트 인스턴스 할당량 ($Instance)"
$q = Aws-Json (@("service-quotas", "list-service-quotas", "--service-code", "sagemaker", "--query", "Quotas[?contains(QuotaName, 'endpoint usage') && contains(QuotaName, '$Instance')].[QuotaName,Value]", "--output", "text") + $base)
if ($q.ok) {
  if ($q.text -eq "") { Write-Host "이름에 '$Instance' 와 'endpoint usage' 가 들어간 할당량을 찾지 못했습니다. 콘솔 Service Quotas > SageMaker 에서 확인하세요."; $unk += "할당량 항목을 못 찾음" }
  else {
    Write-Host $q.text
    foreach ($line in ($q.text -split "`r?`n")) {
      $val = ($line -split "`t")[-1]
      $num = 0.0
      if (-not [double]::TryParse($val, [ref]$num)) { continue }
      if ($num -lt 1) { Write-Host "값이 0 입니다 ($line). 이 인스턴스로는 엔드포인트를 만들 수 없습니다. 할당량 증설을 요청해야 합니다 (승인에 시간이 걸릴 수 있음)."; $warn += "$Instance 엔드포인트 할당량 0" }
      else { $ok += "$Instance 엔드포인트 할당량 $num" }
    }
  }
} else { Write-Host $q.text; $unk += "서비스 할당량 조회 (권한 없음 가능). 콘솔 Service Quotas 에서 확인" }

if ($Bucket -ne "") {
  Section "S3 버킷 $Bucket"
  $hb = Aws-Json @("s3api", "head-bucket", "--bucket", $Bucket)
  if ($hb.ok) {
    $ok += "버킷 접근 가능"
    Write-Host "접근 가능"
    $pb = Aws-Json @("s3api", "get-public-access-block", "--bucket", $Bucket, "--query", "PublicAccessBlockConfiguration", "--output", "json")
    if ($pb.ok) {
      $c = $pb.text | ConvertFrom-Json
      if ($c.BlockPublicAcls -and $c.IgnorePublicAcls -and $c.BlockPublicPolicy -and $c.RestrictPublicBuckets) { Write-Host "퍼블릭 액세스 차단: 모두 켜짐"; $ok += "버킷 비공개 설정" }
      else { Write-Host $pb.text; $warn += "버킷 퍼블릭 액세스 차단이 모두 켜져 있지 않음 (AI Hub 데이터는 비공개로 둬야 함)" }
    } else { Write-Host $pb.text; $unk += "버킷 퍼블릭 액세스 차단 설정을 읽지 못함" }
  } else { Write-Host $hb.text; $warn += "버킷 $Bucket 에 접근 못 함 (없거나 권한 없음)" }
}

Section "요약"
foreach ($x in $ok)   { Write-Host "[확인됨]    $x" }
foreach ($x in $warn) { Write-Host "[문제]      $x" }
foreach ($x in $unk)  { Write-Host "[확인 못 함] $x" }
Write-Host ""
Write-Host "다음: 더미 모델로 배포 시험 (비용은 엔드포인트가 켜져 있는 동안만 나가며, 끝나면 sm_deploy.py delete 로 지웁니다)."
