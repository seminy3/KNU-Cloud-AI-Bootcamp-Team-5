# ML: 수어 단어 분류 모델

환자의 수어 한 단어를 `(30, 49, 2)` 좌표 시퀀스로 받아 178개 단어 중 후보를 돌려주는 모델과, 학습·배포 코드다. 입력 규격은 [`frontend/docs/ml/INPUT_SPEC.md`](../frontend/docs/ml/INPUT_SPEC.md)를 따른다.

## 구성

| 경로 | 내용 |
|---|---|
| `models/final_model_cnn_spec2d_c/` | 최종 모델. `model.pt`(약 14MB)와 `meta.json`(클래스 목록, 정규화 평균·표준편차, 학습 설정) |
| `deploy/code/` | SageMaker 모델 tar에 들어가는 추론 코드. `inference.py`(model_fn·input_fn·predict_fn·output_fn), `sign_features.py`, `spec2d.py` |
| `deploy/make_model_tar.py` | `model.pt`, `meta.json`, `code/`를 SageMaker용 `model.tar.gz`로 묶고 다시 읽어 검사한다 |
| `deploy/sm_deploy.py` | 모델 tar 업로드, 엔드포인트 생성·호출·삭제(boto3). 자격 증명은 AWS CLI 설정을 쓴다 |
| `deploy/serve_local.py`, `deploy/test_local.py` | AWS 없이 같은 호출 계약으로 추론 코드를 검증한다 |
| `deploy/train_final_2d.py` | 최종 모델 학습 스크립트(`--holdout`으로 일부 수어자를 빼고 평가할 수도 있다) |
| `train_seq.py`, `train_svm.py`, `metrics.py`, `perturb.py` | 학습 스크립트가 불러 쓰는 모듈 |
| `build_final.py` | 학습용 CSV 데이터셋 만들기(가로세로비 보정 포함) |
| `deploy/eval_*.py` | 팀 데이터·웹캠 녹화·확률 임계값 평가 |
| `deploy/upload_data.py`, `deploy/aws_preflight.ps1`, `deploy/make_dummy_model.py` | 데이터 S3 업로드, AWS 준비 점검, 배포 시험용 더미 모델 |

## 최종 모델

- 구조: CNN, 입력 `spec2d_noface`(92개 값, 30프레임), 은닉 512, dropout 0.4, 클래스 178개
- 학습: 전체 수어자의 영상으로 학습(평가용으로 뺀 수어자 없음), 시드 42, 학습 샘플 수와 설정은 `meta.json`의 `train`에 있다
- 엔드포인트용으로 묶은 `final_model.tar.gz` 안의 `model.pt`와 같은 파일이다(md5 `e748f9b06e160e0f4e719f3c71086c46`). `meta.json`은 학습 때 기록된 로컬 폴더 경로(`train.data_dirs`, `train.team_dir`)를 폴더 이름만 남기고 지웠다. 추론은 이 항목을 쓰지 않는다.
- 이 모델은 모든 수어자로 학습했으므로 새 수어자에 대한 정확도 근거로 쓸 수 없다. 그 평가는 `python deploy/train_final_2d.py --holdout REAL02,REAL06 --save --out_dir <폴더>`로 두 수어자를 빼고 따로 학습한 모델로 한다.

## 모델 tar 만들기

```
python deploy/make_model_tar.py --model_dir models/final_model_cnn_spec2d_c --out final_model.tar.gz
```

tar 구조는 `model.pt`, `meta.json`, `code/inference.py`, `code/sign_features.py`, `code/spec2d.py`이다.

## 이 폴더가 다루지 않는 것

- **학습 데이터**: AI Hub 수어 영상과 그 좌표 CSV, 팀 데이터셋은 레포에 넣지 않는다. 학습·평가 스크립트는 `--data_dir`, `--team_dir` 같은 인자로 경로를 받는다.
- **확인 범위**: 추론 코드로 `model.pt`를 불러 요청 하나를 처리하는 것까지 확인했다. 학습·배포 스크립트는 원래 폴더 구조(이 폴더의 상위에 데이터 폴더가 있는 구조)에서 쓰던 그대로 옮겼고, 이 레포 구조에서는 아직 돌려 보지 않았다. 기본 경로가 Windows 형식(`..\gesture_dataset_final_ar` 등)이라 다른 환경에서는 경로 인자를 직접 줘야 한다.

필요한 패키지는 `requirements.txt`에 있다.
