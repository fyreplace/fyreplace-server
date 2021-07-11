from concurrent import futures
from datetime import timedelta
from importlib import import_module
from inspect import getmembers
from typing import Any, Iterator, Optional, Tuple, Type

import grpc
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.utils.timezone import now
from google.protobuf.message import Message
from grpc_interceptor.exceptions import Unauthenticated
from psutil import cpu_count

from users.models import Connection
from users.tasks import use_connection

from . import jwt
from .interceptors import AuthorizationInterceptor, ExceptionInterceptor
from .services import get_servicer_interfaces

User = get_user_model()


def create_server(debug: bool = settings.DEBUG) -> grpc.Server:
    services = list(all_servicers())
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=1 if debug else cpu_count()),
        interceptors=(
            ExceptionInterceptor(),
            AuthorizationInterceptor(services),
        ),
    )

    if debug:
        server_creds = grpc.local_server_credentials()
    else:
        ssl = (settings.SSL_PRIVATE_KEY, settings.SSL_CERTIFICATE_CHAIN)
        server_creds = grpc.ssl_server_credentials([ssl])

    server.add_secure_port(settings.GRPC_URL, server_creds)
    _add_services_to_server(services, server)
    return server


def get_user(context: grpc.ServicerContext) -> Optional[User]:
    if token := get_token(context):
        context.caller, context.caller_connection = get_info_from_token(token)
        return context.caller
    else:
        return None


def get_token(context: grpc.ServicerContext) -> Optional[str]:
    metadata = dict(context.invocation_metadata())

    if not (token := metadata.get("authorization")):
        return None

    token_parts = token.split(" ")

    if len(token_parts) != 2 or token_parts[0] != "Bearer":
        raise Unauthenticated("parse_error")

    return token_parts[1]


def get_info_from_token(token: str) -> Tuple[User, Optional[Connection]]:
    try:
        claims = jwt.decode(token)
        user = User.objects.get(id=claims["user_id"])
        connection = None

        if connection_id := claims.get("connection_id"):
            connection = Connection.objects.get(id=connection_id)

            if connection.user_id == user.id:
                use_connection.delay(connection_id=connection_id)
            else:
                raise Unauthenticated("user_id_connection_id_mismatch")
        elif timestamp := claims.get("timestamp"):
            deadline = now() - timedelta(hours=1)

            if timestamp < deadline.timestamp():
                raise Unauthenticated("timestamp_exceeded")
        else:
            raise Unauthenticated("missing_connection_id_or_timestamp")

        return user, connection
    except KeyError:
        raise Unauthenticated("missing_user_id")
    except (jwt.InvalidTokenError, ObjectDoesNotExist):
        raise Unauthenticated("invalid_token")


def serialize_message(message: Message) -> dict:
    data = {}

    for field in message.DESCRIPTOR.fields_by_name.keys():
        data[field] = getattr(message, field)

    return data


def all_servicers() -> Iterator[Type[Any]]:
    for app in apps.get_app_configs():
        try:
            services_module = import_module(app.module.__name__ + ".services")
        except (ImportError, AttributeError):
            continue

        for _, entity in getmembers(services_module):
            if len(get_servicer_interfaces(entity)) > 0:
                yield entity


def _add_services_to_server(services: Iterator[Type[Any]], server: grpc.Server):
    for service in services:
        servicers = get_servicer_interfaces(service)

        for servicer in servicers:
            addition = f"add_{servicer.__name__}_to_server"
            module = import_module(servicer.__module__)

            for name, entity in getmembers(module):
                if name == addition and callable(entity):
                    entity(servicer=service(), server=server)
