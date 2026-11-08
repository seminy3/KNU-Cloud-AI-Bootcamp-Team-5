// 테스트 중에는 Lambda Layer 번들링이 pip install(네트워크)을 돌리지 않도록 한다.
// flow1-backend-stack 의 pythonLayerAsset 이 이 플래그를 보고 requirements 만 복사한다.
process.env.SKIP_LAYER_INSTALL = "1";
