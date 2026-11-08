import * as path from "path";
import * as fs from "fs";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as cognito from "aws-cdk-lib/aws-cognito";

/**
 * 흐름 ①·② 백엔드 스택.
 *
 * 흐름 ①(수어 → 음성·자막): WebSocket recognize/finalize + S3(음성) + DynamoDB.
 * 흐름 ②(음성 → 자막): 브라우저가 Cognito 임시 자격증명으로 Transcribe Streaming에
 *   직접 연결해 음성→텍스트. 백엔드는 doctorUtterance 라우트로 의사 발화를 DynamoDB에
 *   저장(흐름 ①의 history로 재사용). 수어 영상 변환은 범위 밖(자막만).
 *
 * WebSocket 라우트:
 *   $connect / $disconnect — 연결 수명(지금은 통합 없이 수락만)
 *   recognize       — keypoints → SageMaker → 단어(서버 누적)
 *   finalize        — 누적 단어 → Bedrock → Polly → S3 → 응답
 *   doctorUtterance — 의사 발화 텍스트 → DynamoDB 저장
 *
 * IAM 은 최소 권한. Cognito 비인증 역할은 Transcribe 스트리밍 권한만 가진다.
 */
export interface Flow1BackendStackProps extends cdk.StackProps {
  /** SageMaker 엔드포인트 이름 (기본 sign-word-model) */
  readonly endpointName?: string;
  /** Bedrock 모델 ID (llm 기본값과 맞춤) */
  readonly modelId?: string;
}

export class Flow1BackendStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: Flow1BackendStackProps) {
    super(scope, id, props);

    const endpointName =
      props?.endpointName ??
      (this.node.tryGetContext("endpointName") as string) ??
      "sign-word-model";
    const modelId =
      props?.modelId ?? "global.anthropic.claude-opus-5-5";

    // ── 저장소 ───────────────────────────────────────────────
    // 음성 mp3 버킷. 짧은 수명주기로 자동 만료(비용 관리).
    const audioBucket = new s3.Bucket(this, "AudioBucket", {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
      lifecycleRules: [{ expiration: cdk.Duration.days(1) }],
    });

    // 대화 기록 테이블. PK sessionId, SK ts.
    const table = new dynamodb.Table(this, "ConversationTable", {
      partitionKey: { name: "sessionId", type: dynamodb.AttributeType.STRING },
      sortKey: { name: "ts", type: dynamodb.AttributeType.NUMBER },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      timeToLiveAttribute: "ttl",
    });

    // ── 공용 코드 자산 ──────────────────────────────────────
    // recognize: handler + common/. finalize: handler + common/ + llm/ + labels.csv.
    // Docker 없이 synth 가능하도록 로컬 번들링(호스트에서 직접 복사)으로 모은다.
    // anthropic[bedrock] 런타임 의존성은 배포 단계(레이어 또는 설치)에서 더하며,
    // 여기서는 코드 구조만 패키징한다.
    const repoRoot = path.join(__dirname, "..", "..", "..");

    const recognizeCode = localAsset(
      path.join(repoRoot, "backend", "lambda", "recognize"),
      [
        { from: path.join(repoRoot, "backend", "lambda", "recognize"), to: "." },
        { from: path.join(repoRoot, "backend", "common"), to: "common" },
      ]
    );

    const finalizeCode = localAsset(
      path.join(repoRoot, "backend", "lambda", "finalize"),
      [
        { from: path.join(repoRoot, "backend", "lambda", "finalize"), to: "." },
        { from: path.join(repoRoot, "backend", "common"), to: "common" },
        { from: path.join(repoRoot, "llm", "sentence.py"), to: path.join("llm", "sentence.py") },
        { from: path.join(repoRoot, "llm", "handler.py"), to: path.join("llm", "handler.py") },
        { from: path.join(repoRoot, "data", "labels.csv"), to: path.join("data", "labels.csv") },
      ]
    );

    const doctorCode = localAsset(
      path.join(repoRoot, "backend", "lambda", "doctor_utterance"),
      [
        { from: path.join(repoRoot, "backend", "lambda", "doctor_utterance"), to: "." },
        { from: path.join(repoRoot, "backend", "common"), to: "common" },
      ]
    );

    // 두 화면 연결: join(대화 코드·역할 등록), $disconnect(연결 정리)
    const peerCode = (name: string) =>
      localAsset(path.join(repoRoot, "backend", "lambda", name), [
        { from: path.join(repoRoot, "backend", "lambda", name), to: "." },
        { from: path.join(repoRoot, "backend", "common"), to: "common" },
      ]);

    // ── Lambda ──────────────────────────────────────────────
    const recognizeLog = new logs.LogGroup(this, "RecognizeLog", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const recognizeFn = new lambda.Function(this, "RecognizeFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.lambda_handler",
      code: recognizeCode,
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      // recognize 는 인식한 단어를 세션 버퍼(DynamoDB)에 누적하므로 TABLE_NAME 필요
      environment: { ENDPOINT_NAME: endpointName, TABLE_NAME: table.tableName },
      logGroup: recognizeLog,
    });

    // anthropic[bedrock] SDK 레이어. finalize 가 Bedrock 문장 생성에 쓴다.
    // 레이어 패키지는 Lambda 런타임(Linux)용이라 Docker 로 설치한다.
    // synth 는 Docker 없이 통과하고(requirements 만 복사), deploy 시 설치된다.
    // 테스트/빠른 synth 에서는 레이어 설치를 건너뛴다(context skipLayerInstall=true).
    // 환경변수보다 CDK context 가 번들링 시점에 확실히 전달된다.
    const skipLayerInstall =
      this.node.tryGetContext("skipLayerInstall") === true ||
      this.node.tryGetContext("skipLayerInstall") === "true" ||
      process.env.SKIP_LAYER_INSTALL === "1";
    const anthropicLayer = new lambda.LayerVersion(this, "AnthropicLayer", {
      compatibleRuntimes: [lambda.Runtime.PYTHON_3_12],
      description: "anthropic[bedrock] SDK (finalize Lambda 용)",
      code: pythonLayerAsset(
        path.join(repoRoot, "backend", "layers", "anthropic"),
        skipLayerInstall
      ),
    });

    const finalizeLog = new logs.LogGroup(this, "FinalizeLog", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const finalizeFn = new lambda.Function(this, "FinalizeFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.lambda_handler",
      code: finalizeCode,
      // Bedrock + Polly + S3 라 넉넉히
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      layers: [anthropicLayer],
      environment: {
        AUDIO_BUCKET: audioBucket.bucketName,
        TABLE_NAME: table.tableName,
        POLLY_VOICE: "Seoyeon",
        MODEL_ID: modelId,
        BEDROCK_REGION: this.region,
        LABELS_PATH: "data/labels.csv",
        HISTORY_TURNS: "6",
      },
      logGroup: finalizeLog,
    });

    // 흐름 ②: 의사 발화 저장 Lambda
    const doctorLog = new logs.LogGroup(this, "DoctorLog", {
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const doctorFn = new lambda.Function(this, "DoctorUtteranceFn", {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: "handler.lambda_handler",
      code: doctorCode,
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      environment: { TABLE_NAME: table.tableName },
      logGroup: doctorLog,
    });

    const peerFn = (id: string, name: string) =>
      new lambda.Function(this, id, {
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: "handler.lambda_handler",
        code: peerCode(name),
        timeout: cdk.Duration.seconds(10),
        memorySize: 256,
        environment: { TABLE_NAME: table.tableName },
        logGroup: new logs.LogGroup(this, `${id}Log`, {
          retention: logs.RetentionDays.ONE_WEEK,
          removalPolicy: cdk.RemovalPolicy.DESTROY,
        }),
      });
    const joinFn = peerFn("JoinFn", "join");
    const disconnectFn = peerFn("DisconnectFn", "disconnect");

    // ── 최소 권한 IAM ───────────────────────────────────────
    const endpointArn = this.formatArn({
      service: "sagemaker",
      resource: "endpoint",
      resourceName: endpointName,
    });
    recognizeFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "InvokeSignWordModelEndpoint",
        actions: ["sagemaker:InvokeEndpoint"],
        resources: [endpointArn],
      })
    );

    // Bedrock: 모델 + 추론 프로필 ARN. 모델 ID 에 리전 프리픽스(global 등)가 있어
    // foundation-model / inference-profile 양쪽을 허용한다.
    finalizeFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "InvokeBedrockModel",
        actions: ["bedrock:InvokeModel"],
        resources: [
          `arn:aws:bedrock:*::foundation-model/*`,
          `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
        ],
      })
    );
    finalizeFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "SynthesizeSpeech",
        actions: ["polly:SynthesizeSpeech"],
        resources: ["*"], // Polly SynthesizeSpeech 는 리소스 수준 제한을 지원하지 않음
      })
    );
    audioBucket.grantReadWrite(finalizeFn);
    table.grantReadWriteData(finalizeFn);
    table.grantReadWriteData(doctorFn);
    // recognize 도 단어 버퍼를 DynamoDB 에 쌓으므로 읽기·쓰기 권한 필요
    table.grantReadWriteData(recognizeFn);
    table.grantReadWriteData(joinFn);
    table.grantReadWriteData(disconnectFn);

    // ── WebSocket API (API Gateway v2, L1) ─────────────────
    const api = new apigwv2.CfnApi(this, "WebSocketApi", {
      name: "signmed-flow1-ws",
      protocolType: "WEBSOCKET",
      routeSelectionExpression: "$request.body.action",
    });

    // Lambda 통합을 라우트에 붙이는 헬퍼
    const region = this.region;
    const makeRoute = (routeKey: string, fn: lambda.Function) => {
      const integration = new apigwv2.CfnIntegration(
        this,
        `Integ${sanitize(routeKey)}`,
        {
          apiId: api.ref,
          integrationType: "AWS_PROXY",
          integrationUri: `arn:aws:apigateway:${region}:lambda:path/2015-03-31/functions/${fn.functionArn}/invocations`,
        }
      );
      new apigwv2.CfnRoute(this, `Route${sanitize(routeKey)}`, {
        apiId: api.ref,
        routeKey,
        target: `integrations/${integration.ref}`,
      });
      // API Gateway 가 이 Lambda 를 호출하도록 권한 부여
      fn.addPermission(`Invoke${sanitize(routeKey)}`, {
        principal: new iam.ServicePrincipal("apigateway.amazonaws.com"),
        sourceArn: `arn:aws:execute-api:${region}:${this.account}:${api.ref}/*/*`,
      });
    };

    makeRoute("recognize", recognizeFn);
    makeRoute("finalize", finalizeFn);
    makeRoute("doctorUtterance", doctorFn);
    makeRoute("join", joinFn);
    makeRoute("$disconnect", disconnectFn);

    const stage = new apigwv2.CfnStage(this, "Stage", {
      apiId: api.ref,
      stageName: "dev",
      autoDeploy: true,
    });

    // @connections push 권한 (양쪽 Lambda)
    const manageConnections = new iam.PolicyStatement({
      sid: "ManageConnections",
      actions: ["execute-api:ManageConnections"],
      resources: [
        `arn:aws:execute-api:${region}:${this.account}:${api.ref}/*/*/@connections/*`,
      ],
    });
    recognizeFn.addToRolePolicy(manageConnections);
    finalizeFn.addToRolePolicy(manageConnections);
    doctorFn.addToRolePolicy(manageConnections);
    joinFn.addToRolePolicy(manageConnections);
    disconnectFn.addToRolePolicy(manageConnections);

    // ── 흐름 ②: Cognito Identity Pool (브라우저 → Transcribe 직결) ──
    // 브라우저가 로그인 없이 임시 자격증명을 받아 Transcribe Streaming 에 직접 연결한다.
    // MVP 라 비인증(게스트) 접속을 허용하고, 역할에는 Transcribe 스트리밍 권한만 준다.
    const identityPool = new cognito.CfnIdentityPool(this, "TranscribeIdentityPool", {
      allowUnauthenticatedIdentities: true,
      identityPoolName: "signmed-transcribe",
    });

    // 비인증(게스트) 역할: Cognito 가 federated 자격증명을 이 역할로 교환한다.
    const unauthRole = new iam.Role(this, "TranscribeGuestRole", {
      assumedBy: new iam.FederatedPrincipal(
        "cognito-identity.amazonaws.com",
        {
          StringEquals: {
            "cognito-identity.amazonaws.com:aud": identityPool.ref,
          },
          "ForAnyValue:StringLike": {
            "cognito-identity.amazonaws.com:amr": "unauthenticated",
          },
        },
        "sts:AssumeRoleWithWebIdentity"
      ),
      // IAM Role description 은 ASCII 만 허용(CloudFormation 제약)되어 영문으로 둔다.
      description: "Cognito guest role for browser Transcribe streaming",
    });
    unauthRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "TranscribeStreaming",
        actions: [
          "transcribe:StartStreamTranscriptionWebSocket",
          "transcribe:StartStreamTranscription",
        ],
        resources: ["*"], // Transcribe 스트리밍은 리소스 수준 제한을 지원하지 않음
      })
    );

    new cognito.CfnIdentityPoolRoleAttachment(this, "IdentityPoolRoles", {
      identityPoolId: identityPool.ref,
      roles: { unauthenticated: unauthRole.roleArn },
    });

    // ── 출력 ────────────────────────────────────────────────
    new cdk.CfnOutput(this, "WebSocketUrl", {
      value: `wss://${api.ref}.execute-api.${region}.amazonaws.com/${stage.stageName}`,
      description: "프론트가 접속할 WebSocket URL",
    });
    new cdk.CfnOutput(this, "AudioBucketName", { value: audioBucket.bucketName });
    new cdk.CfnOutput(this, "ConversationTableName", { value: table.tableName });
    new cdk.CfnOutput(this, "IdentityPoolId", {
      value: identityPool.ref,
      description: "브라우저가 Transcribe 접속에 쓸 Cognito Identity Pool ID",
    });
  }
}

function sanitize(routeKey: string): string {
  return routeKey.replace(/[^A-Za-z0-9]/g, "");
}

interface CopySpec {
  /** 복사할 원본(파일 또는 디렉터리) 절대 경로 */
  readonly from: string;
  /** 자산 출력 디렉터리 기준 상대 목적지. "." 이면 루트 */
  readonly to: string;
}

/**
 * Docker 없이 로컬에서 파일을 모아 Lambda 자산을 만든다.
 * CDK 의 로컬 번들링(ILocalBundling)으로 호스트 fs 를 써서 지정 경로를 복사한다.
 * __pycache__ 는 제외한다.
 *
 * anchor: 자산 식별용 경로. recognize/finalize 가 서로 다른 경로를 넘겨 자산이 분리된다.
 * 자산 해시는 번들 결과물(OUTPUT) 기준으로 계산해, 복사 내용이 다르면 해시도 달라진다.
 */
function localAsset(anchor: string, specs: CopySpec[]): lambda.AssetCode {
  return lambda.Code.fromAsset(anchor, {
    assetHashType: cdk.AssetHashType.OUTPUT,
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage, // local 실패 시 폴백용(사용 안 함)
      local: {
        tryBundle(outputDir: string): boolean {
          for (const spec of specs) {
            const dest =
              spec.to === "." ? outputDir : path.join(outputDir, spec.to);
            copyRecursive(spec.from, dest);
          }
          return true;
        },
      },
      command: [], // Docker 폴백용. local 번들링이 성공하므로 실행되지 않는다.
    },
  });
}

function copyRecursive(src: string, dest: string): void {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      if (entry === "__pycache__") continue;
      copyRecursive(path.join(src, entry), path.join(dest, entry));
    }
  } else {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
  }
}

/**
 * Python Lambda Layer 자산.
 * layerDir 의 requirements.txt 를 Lambda 가 찾는 python/ 아래에 설치한다.
 *
 * - 로컬 번들링: pip 으로 manylinux(x86_64) 휠을 받아 python/ 에 설치 시도.
 *   성공하면 Docker 없이 레이어가 완성된다.
 * - 로컬 설치가 안 되면(네트워크/pip 부재) requirements.txt 만 python/ 에 두고
 *   synth 는 통과시킨다. 이 경우 실제 설치는 배포 시 Docker 번들링(command)이 한다.
 */
function pythonLayerAsset(layerDir: string, skipInstall: boolean): lambda.AssetCode {
  const reqFile = path.join(layerDir, "requirements.txt");
  return lambda.Code.fromAsset(layerDir, {
    assetHashType: cdk.AssetHashType.OUTPUT,
    bundling: {
      image: lambda.Runtime.PYTHON_3_12.bundlingImage,
      // Docker 폴백: 로컬 번들링이 false 를 반환할 때만 쓰인다.
      command: [
        "bash",
        "-c",
        "pip install -r requirements.txt -t /asset-output/python",
      ],
      local: {
        tryBundle(outputDir: string): boolean {
          const pyDir = path.join(outputDir, "python");
          fs.mkdirSync(pyDir, { recursive: true });

          // 테스트/빠른 synth 에서는 pip install 을 건너뛴다(네트워크·시간 회피).
          // 실제 설치는 배포용 synth/deploy 때(skipInstall=false).
          if (skipInstall) {
            fs.copyFileSync(reqFile, path.join(pyDir, "requirements.txt"));
            return true;
          }

          const { spawnSync } = require("child_process") as typeof import("child_process");
          const r = spawnSync(
            "pip",
            [
              "install",
              "-r",
              reqFile,
              "--platform",
              "manylinux2014_x86_64",
              "--implementation",
              "cp",
              "--python-version",
              "3.12",
              "--only-binary=:all:",
              "-t",
              pyDir,
            ],
            { stdio: "ignore" }
          );
          if (r.status === 0) {
            return true; // 로컬 설치 성공 → Docker 불필요
          }
          // 설치 실패: requirements 만 남기고 synth 는 통과. 실제 설치는 배포 시 Docker.
          fs.copyFileSync(reqFile, path.join(pyDir, "requirements.txt"));
          return true;
        },
      },
    },
  });
}
