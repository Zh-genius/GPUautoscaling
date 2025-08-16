# EKS GPU GPU自动伸缩配置

以下是一个简化版的GPU自动伸缩配置，确保版本兼容且步骤简单，可在Mac终端上操作。

## 1. 准备工作

首先安装必要工具：

```bash
# 安装eksctl
brew tap weaveworks/tap
brew install weaveworks/tap/eksctl

# 安装kubectl
brew install kubectl

# 安装helm
brew install helm
```

## 2. 创建EKS集群配置文件

```bash
cat > gpu-cluster.yaml << EOF
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig

metadata:
  name: simple-gpu-cluster
  region: us-east-1

nodeGroups:
  - name: gpu-node-group
    instanceType: g4dn.xlarge  # 选择配额通常较充足的实例类型
    minSize: 1
    maxSize: 3
    desiredCapacity: 1
    labels:
      accelerator: nvidia-gpu
    tags:
      k8s.io/cluster-autoscaler/enabled: "true"
      k8s.io/cluster-autoscaler/simple-gpu-cluster: "true"
EOF
```

## 3. 创建EKS集群

```bash
eksctl create cluster -f gpu-cluster.yaml
```

## 4. 配置kubectl

```bash
aws eks update-kubeconfig --name simple-gpu-cluster --region us-east-1
```

## 5. 安装NVIDIA设备插件

```bash
kubectl create -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.13.0/nvidia-device-plugin.yml
```

## 6. 安装Prometheus和DCGM Exporter

```bash
# 添加Helm仓库
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 安装Prometheus
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false

# 部署DCGM Exporter
cat > dcgm-exporter.yaml << EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: dcgm-exporter
  namespace: monitoring
spec:
  selector:
    matchLabels:
      app: dcgm-exporter
  template:
    metadata:
      labels:
        app: dcgm-exporter
      name: dcgm-exporter
    spec:
      containers:
      - image: nvidia/dcgm-exporter:3.1.6
        name: dcgm-exporter
        ports:
        - containerPort: 9400
          name: metrics
        resources:
          limits:
            nvidia.com/gpu: 1
        volumeMounts:
        - name: pod-gpu-resources
          mountPath: /var/lib/kubelet/pod-resources
      volumes:
      - name: pod-gpu-resources
        hostPath:
          path: /var/lib/kubelet/pod-resources
---
apiVersion: v1
kind: Service
metadata:
  name: dcgm-exporter
  namespace: monitoring
spec:
  selector:
    app: dcgm-exporter
  ports:
  - port: 9400
    targetPort: 9400
EOF

kubectl apply -f dcgm-exporter.yaml

# 创建ServiceMonitor监控DCGM
cat > dcgm-servicemonitor.yaml << EOF
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: dcgm-exporter
  namespace: monitoring
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: dcgm-exporter
  endpoints:
  - port: metrics
EOF

kubectl apply -f dcgm-servicemonitor.yaml
```

## 7. 安装Prometheus Adapter

```bash
# 创建Prometheus Adapter配置
cat > prometheus-adapter-config.yaml << EOF
rules:
- seriesQuery: 'DCGM_FI_DEV_GPU_UTIL{service!=""}'
  resources:
    overrides:
      service: {resource: "service"}
  name:
    matches: "^DCGM_FI_DEV_GPU_UTIL$"
    as: "DCGM_FI_DEV_GPU_UTIL_AVG"
  metricsQuery: avg(<<.Series>>{<<.LabelMatchers>>}) by (service)
EOF

# 安装Prometheus Adapter
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring \
  --set prometheus.url=http://prometheus-kube-prometheus-prometheus.monitoring.svc:9090 \
  --set config.file=prometheus-adapter-config.yaml
```

## 8. 创建测试GPU应用

```bash
# 创建Dockerfile
cat > Dockerfile << EOF
FROM nvidia/cuda:11.7.1-runtime-ubuntu20.04

RUN apt-get update && apt-get install -y python3 python3-pip
RUN pip3 install flask gpustat

WORKDIR /app
COPY app.py .

EXPOSE 8000
CMD ["python3", "app.py"]
EOF

# 创建应用代码
cat > app.py << EOF
from flask import Flask
import gpustat
import time
import threading

app = Flask(__name__)
gpu_usage = 0

def gpu_load_generator():
    global gpu_usage
    while True:
        # 模拟GPU负载变化
        stats = gpustat.GPUStatCollection.new_query()
        for gpu in stats:
            gpu_usage = gpu.utilization
        time.sleep(5)

# 启动后台线程生成GPU负载
thread = threading.Thread(target=gpu_load_generator, daemon=True)
thread.start()

@app.route('/')
def index():
    return f"GPU Usage: {gpu_usage}%"

@app.route('/load')
def load():
    # 增加GPU负载
    import torch
    a = torch.randn(10000, 10000, device='cuda')
    b = torch.randn(10000, 10000, device='cuda')
    for _ in range(100):
        c = torch.matmul(a, b)
    return "GPU load generated"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
EOF

# 构建并推送镜像到ECR
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-east-1

aws ecr create-repository --repository-name gpu-test-app --region $REGION

docker build -t gpu-test-app .
docker tag gpu-test-app:latest $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/gpu-test-app:latest

aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
docker push $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/gpu-test-app:latest

# 创建Kubernetes部署配置
cat > gpu-app.yaml << EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gpu-test-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: gpu-test-app
  template:
    metadata:
      labels:
        app: gpu-test-app
    spec:
      containers:
      - name: gpu-test-app
        image: $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/gpu-test-app:latest
        ports:
        - containerPort: 8000
        resources:
          limits:
            nvidia.com/gpu: 1
---
apiVersion: v1
kind: Service
metadata:
  name: gpu-test-service
spec:
  selector:
    app: gpu-test-app
  ports:
  - port: 80
    targetPort: 8000
EOF

kubectl apply -f gpu-app.yaml
```

## 9. 创建HPA配置

```bash
cat > gpu-hpa.yaml << EOF
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gpu-test-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: gpu-test-app
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: Object
    object:
      metric:
        name: DCGM_FI_DEV_GPU_UTIL_AVG
      describedObject:
        kind: Service
        name: gpu-test-service
      target:
        type: Value
        value: "50"
EOF

kubectl apply -f gpu-hpa.yaml
```

## 10. 安装集群自动伸缩器

```bash
# 创建IAM策略
POLICY_ARN=$(aws iam create-policy \
  --policy-name ClusterAutoscalerPolicy \
  --policy-document https://raw.githubusercontent.com/kubernetes/autoscaler/master/cluster-autoscaler/cloudprovider/aws/examples/cluster-autoscaler-policy.json \
  --query 'Policy.Arn' --output text)

# 绑定策略到节点组角色
eksctl create iamserviceaccount \
  --cluster=simple-gpu-cluster \
  --namespace=kube-system \
  --name=cluster-autoscaler \
  --attach-policy-arn=$POLICY_ARN \
  --override-existing-serviceaccounts \
  --approve

# 部署集群自动伸缩器
kubectl apply -f - << EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cluster-autoscaler
  namespace: kube-system
  labels:
    app: cluster-autoscaler
spec:
  replicas: 1
  selector:
    matchLabels:
      app: cluster-autoscaler
  template:
    metadata:
      labels:
        app: cluster-autoscaler
    spec:
      serviceAccountName: cluster-autoscaler
      containers:
      - image: k8s.gcr.io/autoscaling/cluster-autoscaler:v1.21.0
        name: cluster-autoscaler
        resources:
          limits:
            cpu: 100m
            memory: 300Mi
          requests:
            cpu: 100m
            memory: 300Mi
        command:
        - ./cluster-autoscaler
        - --v=4
        - --stderrthreshold=info
        - --cloud-provider=aws
        - --skip-nodes-with-local-storage=false
        - --expander=least-waste
        - --balance-similar-node-groups
        - --skip-nodes-with-system-pods=false
        - --cluster-name=simple-gpu-cluster
EOF

# 添加自动伸缩器权限
kubectl annotate deployment cluster-autoscaler \
  cluster-autoscaler.kubernetes.io/safe-to-evict="false" \
  -n kube-system
```

## 11. 测试GPU自动伸缩

```bash
# 端口转发以便访问应用
kubectl port-forward service/gpu-test-service 8000:80 &

# 生成GPU负载（在另一个终端执行）
while true; do curl http://localhost:8000/load; sleep 10; done

# 监控HPA状态
watch kubectl get hpa

# 监控pod数量变化
watch kubectl get pods

# 监控节点数量变化
watch kubectl get nodes
```

## 清理资源（测试完成后）

```bash
eksctl delete cluster --name simple-gpu-cluster --region us-east-1
aws ecr delete-repository --repository-name gpu-test-app --force --region us-east-1
aws iam delete-policy --policy-arn $POLICY_ARN
```

