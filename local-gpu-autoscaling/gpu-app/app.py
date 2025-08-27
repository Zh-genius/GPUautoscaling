from flask import Flask
import torch
import time
import random

app = Flask(__name__)

@app.route('/')
def index():
    return "GPU Test API is running"

@app.route('/ping')
def ping():
    return "pong"

@app.route('/gputest')
def gpu_test():
    # 使用GPU进行一些计算
    if torch.cuda.is_available():
        a = torch.randn(10000, 10000, device='cuda')
        b = torch.randn(10000, 10000, device='cuda')
        start = time.time()
        c = torch.matmul(a, b)
        torch.cuda.synchronize()
        end = time.time()
        return f"GPU computation completed in {end - start:.4f} seconds"
    else:
        return "GPU not available"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000)
