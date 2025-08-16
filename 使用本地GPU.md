以下是一个简化版的GPU自动伸缩方案，基于本地Kubernetes环境（无需AWS/EKS），专为Mac终端设计，确保版本兼容且配置精简：

### 1. 准备环境（Mac终端执行）

首先确保你的Mac有NVIDIA GPU并已安装相关驱动，然后执行以下命令：

```bash
# 安装minikube（本地K8s集群）
brew install minikube

# 安装kubectl
brew install kubectl

# 安装helm
brew install helm

# 启动带GPU支持的minikube集群（单节点配置）
minikube start --driver=docker --gpu --kubernetes-version=1.26.0 --cpus=4 --memory=8g --disk-size=30g

# 验证集群状态
minikube status
kubectl get nodes
```

### 2. 安装NVIDIA设备插件

```bash
# 创建NVIDIA设备插件
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nvidia-device-plugin-daemonset
  namespace: kube-system
spec:
  selector:
    matchLabels:
      name: nvidia-device-plugin-ds
  template:
    metadata:
      labels:
        name: nvidia-device-plugin-ds
    spec:
      tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
      containers:
      - image: nvidia/k8s-device-plugin:v0.14.1
        name: nvidia-device-plugin-ctr
        securityContext:
          allowPrivilegeEscalation: false
          capabilities:
            drop: ["ALL"]
        volumeMounts:
        - name: device-plugin
          mountPath: /var/lib/kubelet/device-plugins
      volumes:
      - name: device-plugin
        hostPath:
          path: /var/lib/kubelet/device-plugins
EOF

# 验证GPU是否可用
kubectl get nodes "-o=custom-columns=NAME:.metadata.name,GPUS:.status.allocatable.nvidia\.com/gpu"
```

### 3. 部署监控组件

```bash
# 添加Prometheus仓库并更新
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 安装Prometheus Stack（兼容版本）
helm install prometheus prometheus-community/kube-prometheus-stack \
  --version 45.21.0 \
  --namespace monitoring \
  --create-namespace \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.enabled=true

# 部署DCGM Exporter收集GPU指标
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: dcgm-exporter
  namespace: monitoring
  labels:
    app: dcgm-exporter
spec:
  selector:
    matchLabels:
      app: dcgm-exporter
  template:
    metadata:
      labels:
        app: dcgm-exporter
    spec:
      tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
      containers:
      - image: nvidia/dcgm-exporter:3.1.6
        name: dcgm-exporter
        ports:
        - containerPort: 9400
          name: metrics
        resources:
          limits:
            nvidia.com/gpu: 1
---
apiVersion: v1
kind: Service
metadata:
  name: dcgm-exporter
  namespace: monitoring
  labels:
    app: dcgm-exporter
spec:
  selector:
    app: dcgm-exporter
  ports:
  - port: 9400
    targetPort: 9400
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: dcgm-exporter
  namespace: monitoring
  labels:
    app: dcgm-exporter
spec:
  selector:
    matchLabels:
      app: dcgm-exporter
  endpoints:
  - port: metrics
EOF

# 安装Prometheus Adapter（版本兼容）
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --version 4.2.0 \
  --namespace monitoring \
  --set config.configMaps[0].name=adapter-config \
  --set config.configMaps[0].data.config.yml="$(cat <<EOF
rules:
- seriesQuery: 'DCGM_FI_DEV_GPU_UTIL{namespace!="",pod!=""}'
  resources:
    overrides:
      namespace: {resource: "namespace"}
      pod: {resource: "pod"}
  name:
    matches: "^DCGM_FI_DEV_GPU_UTIL$"
    as: "gpu_utilization"
  metricsQuery: "avg(DCGM_FI_DEV_GPU_UTIL) by (namespace, pod)"
EOF
)"
```

### 4. 部署测试应用和HPA

```bash
# 创建测试命名空间
kubectl create namespace gpu-test

# 部署GPU测试应用
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gpu-test-app
  namespace: gpu-test
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
      - name: gpu-test
        image: nvidia/cuda:12.2.0-runtime-ubuntu22.04
        command: ["sh", "-c", "while true; do nvidia-smi; sleep 10; done"]
        resources:
          limits:
            nvidia.com/gpu: 1
---
apiVersion: v1
kind: Service
metadata:
  name: gpu-test-service
  namespace: gpu-test
spec:
  selector:
    app: gpu-test-app
  ports:
  - port: 80
    targetPort: 80
EOF

# 创建HPA配置
cat <<EOF | kubectl apply -f -
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gpu-test-hpa
  namespace: gpu-test
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: gpu-test-app
  minReplicas: 1
  maxReplicas: 3
  metrics:
  - type: Pods
    pods:
      metric:
        name: gpu_utilization
      target:
        type: AverageValue
        averageValue: 30
EOF
```

### 5. 验证配置

```bash
# 检查所有组件是否运行正常
kubectl get pods -n monitoring
kubectl get pods -n gpu-test

# 检查HPA状态
kubectl get hpa -n gpu-test

# 查看GPU指标
kubectl get --raw "/apis/custom.metrics.k8s.io/v1beta1/namespaces/gpu-test/pods/*/gpu_utilization" | jq .
```

### 6. 测试自动伸缩

```bash
# 端口转发Prometheus以便查看指标
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9090:9090 &

# 端口转发Grafana（用户名admin，密码获取见下方）
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80 &

# 获取Grafana密码
kubectl get secret -n monitoring prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 --decode ; echo

# 增加GPU负载（新开终端执行）
GPU_POD=$(kubectl get pods -n gpu-test -l app=gpu-test-app -o jsonpath="{.items[0].metadata.name}")
kubectl exec -it -n gpu-test $GPU_POD -- sh -c "apt-get update && apt-get install -y stress-ng && stress-ng --gpu 1 --gpu-load 80 --timeout 5m"

# 观察HPA是否触发伸缩
kubectl get hpa -n gpu-test -w
```

### 清理资源（测试完成后）

```bash
# 停止端口转发
pkill kubectl

# 删除资源
kubectl delete namespace gpu-test
helm uninstall prometheus-adapter -n monitoring
helm uninstall prometheus -n monitoring
kubectl delete daemonset nvidia-device-plugin-daemonset -n kube-system

# 停止minikube
minikube stop

# 如需彻底清理
minikube delete
```

这个方案使用了兼容的版本组合，确保各组件能够协同工作：
- Kubernetes 1.26.0
- NVIDIA设备插件 v0.14.1
- DCGM Exporter 3.1.6
- Prometheus Stack 45.21.0
- Prometheus Adapter 4.2.0
- CUDA 12.2.0

通过增加GPU负载（使用stress-ng），你可以测试HPA是否会根据GPU利用率自动扩展Pod数量，当负载降低时又会自动缩减。
