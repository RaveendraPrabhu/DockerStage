FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
        python3 \
        python3-pip \
        git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt
CMD ["python3", "-m", "myservice"]
