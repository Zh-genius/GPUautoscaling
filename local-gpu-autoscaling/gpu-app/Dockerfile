FROM nvidia/cuda:11.7.1-cudnn8-runtime-ubuntu20.04

WORKDIR /app

RUN apt-get update && apt-get install -y python3 python3-pip
RUN pip3 install flask torch==1.13.1+cu117 -f https://download.pytorch.org/whl/cu117/torch_stable.html

COPY app.py .

CMD ["python3", "app.py"]
