import grpc
from concurrent import futures

try:
    # Running from project root (local dev)
    from grpc_service import users_pb2 as users_pb2
    from grpc_service import users_pb2_grpc as users_pb2_grpc
except ImportError:
    # Running in Docker (/app directory)
    import users_pb2 as users_pb2
    import users_pb2_grpc as users_pb2_grpc

class UserService(users_pb2_grpc.UserServiceServicer):
    def GetUser(self, request, context):
        return users_pb2.UserResponse(id=request.id, name=f"User {request.id}")

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=100))
    users_pb2_grpc.add_UserServiceServicer_to_server(UserService(), server)
    server.add_insecure_port('[::]:50051')
    print("gRPC server listening on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()