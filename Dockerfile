FROM python:3.6.9-slim
LABEL maintainer="ISP RAS"
WORKDIR /app/

RUN apt update &&\
    apt install python3-pip -y

RUN pip3 install --extra-index-url https://test.pypi.org/simple/ clouni-configuration-tool

RUN apt remove python3-pip -y &&\
    apt autoremove -y &&\
    apt clean
EXPOSE 50052
CMD clouni-configuration-tool --host 0.0.0.0 -p 50052 --foreground