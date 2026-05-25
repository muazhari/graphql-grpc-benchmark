from fastapi import FastAPI
from ariadne import QueryType, load_schema_from_path
from ariadne.asgi import GraphQL
from ariadne.contrib.federation import FederatedObjectType, make_federated_schema

# Load the federated schema SDL
type_defs = load_schema_from_path("schema.graphql")

# Define resolvers
query = QueryType()
user = FederatedObjectType("User")


@query.field("users_GetUser")
def resolve_users_get_user(_, info, id):
    return {"id": id, "name": f"Mock User {id}"}


@user.reference_resolver
def resolve_user_reference(_, info, representation):
    user_id = representation["id"]
    return {"id": user_id, "name": f"Mock User {user_id}"}


# Build the federated schema
schema = make_federated_schema(type_defs, [query, user])

# Mount as ASGI app under /graphql
graphql_app = GraphQL(schema)

app = FastAPI()
app.mount("/graphql", graphql_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=4000)
