# GPUautoscaling
# 本地 Kubernetes GPU 自动伸缩示例

本指南将演示如何在本地 Minikube 环境中设置基于 GPU 利用率的自动伸缩，无需依赖 AWS/EKS 等云服务。我们将使用 Prometheus 收集模拟的 GPU 指标，并通过 Horizontal Pod Autoscaler (HPA) 实现 Pod 自动伸缩。

## 前提条件

确保你的系统已安装以下工具：
- Docker（作为 Minikube 的驱动）
- macOS 上的 Homebrew（用于安装工具）

## 步骤 1：安装必要工具

```bash
# 安装 minikube
brew install minikube

# 安装 kubectl
brew install kubectl

# 安装 helm
brew install helm
```

## 步骤 2：启动带 GPU 支持的 Minikube 集群

```bash
minikube start --driver=docker \
  --kubernetes-version=1.26.0 \
  --cpus=2 \  
  --memory=4096m \ 
  --disk-size=20g \
  --container-runtime=containerd
```

验证集群状态：
```bash
# 检查 minikube 状态
minikube status

# 检查节点状态
kubectl get nodes
```

## 步骤 3：部署 Prometheus 监控

我们将部署 Prometheus 来收集模拟的 GPU 指标。

1. 创建监控命名空间：
```bash
kubectl create namespace monitoring
```

2. 安装 Prometheus Stack：
```bash
# 添加 Prometheus 社区 Helm 仓库
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# 安装 Prometheus（禁用 Grafana 以简化设置）
helm install prometheus prometheus-community/kube-prometheus-stack \
  --version 45.21.0 \
  --namespace monitoring \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set grafana.enabled=false \  
  --set prometheus.prometheusSpec.resources.requests.cpu=100m \
  --set prometheus.prometheusSpec.resources.requests.memory=256Mi
```

## 步骤 4：部署模拟 GPU 利用率的服务

我们将部署一个简单的服务来生成模拟的 GPU 利用率指标（0-100 之间的随机值）：

```bash
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mock-gpu-metrics
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mock-gpu-metrics
  template:
    metadata:
      labels:
        app: mock-gpu-metrics
    spec:
      containers:
      - name: metrics-server
        image: nginx:alpine
        ports:
        - containerPort: 80
        # 启动脚本：每5秒生成随机GPU利用率（0-100）
        command: ["sh", "-c", "while true; do \
          echo 'HTTP/1.1 200 OK\n\n# HELP gpu_utilization GPU utilization percentage\n# TYPE gpu_utilization gauge\ngpu_utilization{pod=\"gpu-test-app\"} ' \$((RANDOM % 100)) > /usr/share/nginx/html/metrics; \
          sleep 5; \
          done;"]
---
apiVersion: v1
kind: Service
metadata:
  name: mock-gpu-metrics
  namespace: default
spec:
  selector:
    app: mock-gpu-metrics
  ports:
  - port: 80
    targetPort: 80
---
# 让Prometheus自动发现这个模拟指标服务
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: mock-gpu-monitor
  namespace: monitoring
  labels:
    release: prometheus
spec:
  selector:
    matchLabels:
      app: mock-gpu-metrics
  namespaceSelector:
    matchNames: [default]
  endpoints:
  - port: 80
    path: /metrics
    interval: 5s
EOF
```

## 步骤 5：部署 Prometheus Adapter

Prometheus Adapter 用于将 Prometheus 收集的指标转换为 Kubernetes API 可识别的格式，使 HPA 能够使用这些指标进行自动伸缩。

```bash
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --version 4.2.0 \
  --namespace monitoring \
  --set config.configMaps[0].name=adapter-config \
  --set config.configMaps[0].data.config.yml="$(cat <<EOF
rules:
- seriesQuery: 'gpu_utilization{pod!=""}'
  resources:
    overrides:
      pod: {resource: "pod"}
  name:
    matches: "^gpu_utilization$"
    as: "gpu_utilization_avg"
  metricsQuery: "avg(<<.Series>>) by (pod)"
EOF
)"
```

## 步骤 6：部署测试应用和 HPA

1. 部署需要"GPU"的测试应用：
```bash
cat <<EOF | kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: gpu-test-app
  namespace: default
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
        image: busybox
        command: ["sh", "-c", "while true; do sleep 3600; done"]
EOF
```

2. 创建 HPA 配置（当模拟 GPU 利用率 > 30 时扩容，< 10 时缩容）：
```bash
cat <<EOF | kubectl apply -f -
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: gpu-test-hpa
  namespace: default
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
        name: gpu_utilization_avg
      target:
        type: AverageValue
        averageValue: "30"  # 阈值：超过30扩容
EOF
```

## 步骤 7：验证自动伸缩效果

实时查看 HPA 状态，观察 GPU 利用率和副本数的变化：

```bash
kubectl get hpa gpu-test-hpa -w
```

你应该能看到类似以下的输出，显示当前的 GPU 利用率和副本数量，当利用率超过 30 时会自动增加副本数，低于 10 时会减少副本数：

```
NAME           REFERENCE               TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
gpu-test-hpa   Deployment/gpu-test-app  45/30     1         3         2          5m
```

## 清理环境

当你完成测试后，可以使用以下命令清理环境：

```bash
# 删除 HPA 和测试应用
kubectl delete hpa gpu-test-hpa
kubectl delete deployment gpu-test-app
kubectl delete deployment mock-gpu-metrics
kubectl delete service mock-gpu-metrics
kubectl delete servicemonitor -n monitoring mock-gpu-monitor

# 卸载 Prometheus 和 Adapter
helm uninstall prometheus -n monitoring
helm uninstall prometheus-adapter -n monitoring

# 删除命名空间
kubectl delete namespace monitoring

# 停止并删除 Minikube 集群
minikube stop
minikube delete
```

## 注意事项


- 自动伸缩可能需要几分钟时间才能根据指标变化做出反应
- 可以通过调整 HPA 配置中的阈值（`averageValue`）来改变伸缩灵敏度
- Minikube 资源限制（CPU、内存）可能会影响自动伸缩的效果，可根据实际情况调整
