# Local GPU Autoscaling on Kubernetes
The GPU autoscaling system, which automatically scales GPU-enabled workloads in Kubernetes based on real-time GPU utilization metrics. The system demonstrates how to implement custom metrics-based autoscaling for GPU resources using NVIDIA's Data Center GPU Manager (DCGM) and Kubernetes Horizontal Pod Autoscaler (HPA).

# Architecture Overview

<img width="389" height="618" alt="architect" src="https://github.com/user-attachments/assets/cf410054-c958-46fc-9415-69b43a0e110e" />


Components
Component	Purpose
DCGM Exporter	Collects GPU metrics (utilization, memory, temperature)
Prometheus	Stores and aggregates GPU metrics
Grafana	Visualizes GPU metrics and autoscaling behavior
Metrics Adapter	Converts Prometheus metrics to Kubernetes API format
Horizontal Pod Autoscaler	Automatically scales GPU workloads based on utilization thresholds
NVIDIA Device Plugin	Enables Kubernetes to recognize and manage GPU resources
GPU Test App	Sample application generating GPU load for testing
# Workflow Diagram

Directory Structure<img width="1188" height="219" alt="work flow" src="https://github.com/user-attachments/assets/5e14b666-50ea-40da-9928-7cfc5f0e2ff8" />

local-gpu-autoscaling/
├── clean-prometheus-config.yaml       # Simplified Prometheus scraping config
├── dcgm-exporter.yaml                 # DCGM Exporter deployment
├── metrics-server-values.yaml         # Metrics server configuration
├── prometheus-config.yaml             # Full Prometheus configuration
├── updated-prometheus-config.yaml     # Updated Prometheus scraping rules
├── prometheus/
│   ├── adapter-values.yaml            # Prometheus Adapter custom rules
│   ├── dcgm-exporter.yaml             # Alternative DCGM deployment
│   └── values.yaml                    # Prometheus Helm chart values
├── gpu-app/
│   ├── Dockerfile                     # GPU test application Dockerfile
│   ├── app.py                         # Flask app with GPU workload
│   ├── deployment.yaml                # Kubernetes deployment config
│   └── nvidia-device-plugin.yaml      # NVIDIA device plugin
└── metrics-server/
    └── metrics-server.yaml            # Metrics server deployment
Setup Instructions
Prerequisites
Docker
Minikube
kubectl
Helm
NVIDIA GPU with drivers (for real GPU testing)
Quick Start
Start Minikube with GPU support:
bash
minikube start --driver=docker --gpu --kubernetes-version=1.26.0 --cpus=4 --memory=8g

Deploy NVIDIA device plugin:
bash
kubectl apply -f gpu-app/nvidia-device-plugin.yaml

Deploy monitoring stack:
bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  -f prometheus/values.yaml

Deploy DCGM Exporter:
bash
kubectl apply -f dcgm-exporter.yaml

Deploy metrics adapter:
bash
helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring -f prometheus/adapter-values.yaml

Deploy GPU test application:
bash
# Build the test app image
minikube image build -t gpu-test-app:latest gpu-app/

# Deploy the application
kubectl apply -f gpu-app/deployment.yaml

Monitoring & Visualization
Access Grafana
bash
# Get Grafana password
kubectl get secret -n monitoring prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 --decode ; echo

# Port forward to Grafana
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80
Open http://localhost:3000 in your browser and log in with username admin and the retrieved password.
Key Dashboards
GPU Utilization Dashboard
Tracks GPU utilization across pods
Monitors memory usage and temperature
Shows scaling events over time
Autoscaling Dashboard
Visualizes HPA activity
Displays replica count changes
Correlates load with scaling events
Testing Autoscaling
Generate GPU load:
bash
# Find the GPU pod name
GPU_POD=$(kubectl get pods -l app=gpu-api -o jsonpath="{.items[0].metadata.name}")

# Execute load generator
kubectl exec -it $GPU_POD -- sh -c "apt-get update && apt-get install -y stress-ng && stress-ng --gpu 1 --gpu-load 80 --timeout 5m"

Monitor HPA status:
bash
kubectl get hpa -w

Cleanup
bash
# Uninstall Helm releases
helm uninstall prometheus -n monitoring
helm uninstall prometheus-adapter -n monitoring

# Delete deployments
kubectl delete -f gpu-app/deployment.yaml
kubectl delete -f dcgm-exporter.yaml
kubectl delete -f gpu-app/nvidia-device-plugin.yaml

# Stop Minikube
minikube stop

# Optional: Delete Minikube cluster
minikube delete
Configuration Notes
Adjust HPA thresholds in gpu-app/deployment.yaml (default: 30% utilization)
Modify Prometheus scraping interval in clean-prometheus-config.yaml (default: 15s)
Update GPU resource limits based on your hardware capabilities
