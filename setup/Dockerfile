FROM python:3.13-alpine
# Docker-CLI und Compose-Plugin sprechen über den gemounteten Socket mit dem Host-Daemon
RUN apk add --no-cache docker-cli docker-cli-compose
WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static
USER root
EXPOSE 8080
CMD ["python3", "-u", "/app/app.py"]
