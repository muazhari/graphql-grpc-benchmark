FROM python:3.10-slim

WORKDIR /app

# Install requirements
RUN pip install grpcio grpcio-tools

# Generate python stubs from proto and start server
COPY users.proto server.py ./
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. users.proto

EXPOSE 50051

CMD ["python", "server.py"]