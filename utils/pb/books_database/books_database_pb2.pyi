from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ReadRequest(_message.Message):
    __slots__ = ("title",)
    TITLE_FIELD_NUMBER: _ClassVar[int]
    title: str
    def __init__(self, title: _Optional[str] = ...) -> None: ...

class ReadResponse(_message.Message):
    __slots__ = ("success", "quantity", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    quantity: int
    message: str
    def __init__(self, success: bool = ..., quantity: _Optional[int] = ..., message: _Optional[str] = ...) -> None: ...

class WriteRequest(_message.Message):
    __slots__ = ("title", "quantity")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    title: str
    quantity: int
    def __init__(self, title: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class WriteResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...

class ReplicateWriteRequest(_message.Message):
    __slots__ = ("title", "quantity", "seq", "from_replica")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    SEQ_FIELD_NUMBER: _ClassVar[int]
    FROM_REPLICA_FIELD_NUMBER: _ClassVar[int]
    title: str
    quantity: int
    seq: int
    from_replica: int
    def __init__(self, title: _Optional[str] = ..., quantity: _Optional[int] = ..., seq: _Optional[int] = ..., from_replica: _Optional[int] = ...) -> None: ...

class ReplicateWriteResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...

class WhoIsPrimaryRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class WhoIsPrimaryResponse(_message.Message):
    __slots__ = ("leader_id", "leader_addr")
    LEADER_ID_FIELD_NUMBER: _ClassVar[int]
    LEADER_ADDR_FIELD_NUMBER: _ClassVar[int]
    leader_id: int
    leader_addr: str
    def __init__(self, leader_id: _Optional[int] = ..., leader_addr: _Optional[str] = ...) -> None: ...

class ElectionRequest(_message.Message):
    __slots__ = ("candidate_id",)
    CANDIDATE_ID_FIELD_NUMBER: _ClassVar[int]
    candidate_id: int
    def __init__(self, candidate_id: _Optional[int] = ...) -> None: ...

class ElectionResponse(_message.Message):
    __slots__ = ("alive",)
    ALIVE_FIELD_NUMBER: _ClassVar[int]
    alive: bool
    def __init__(self, alive: bool = ...) -> None: ...

class CoordinatorRequest(_message.Message):
    __slots__ = ("leader_id",)
    LEADER_ID_FIELD_NUMBER: _ClassVar[int]
    leader_id: int
    def __init__(self, leader_id: _Optional[int] = ...) -> None: ...

class HeartbeatRequest(_message.Message):
    __slots__ = ("leader_id",)
    LEADER_ID_FIELD_NUMBER: _ClassVar[int]
    leader_id: int
    def __init__(self, leader_id: _Optional[int] = ...) -> None: ...

class Ack(_message.Message):
    __slots__ = ("ok",)
    OK_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    def __init__(self, ok: bool = ...) -> None: ...

class PrepareItem(_message.Message):
    __slots__ = ("title", "quantity")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    title: str
    quantity: int
    def __init__(self, title: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class PrepareRequest(_message.Message):
    __slots__ = ("order_id", "items")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    items: _containers.RepeatedCompositeFieldContainer[PrepareItem]
    def __init__(self, order_id: _Optional[str] = ..., items: _Optional[_Iterable[_Union[PrepareItem, _Mapping]]] = ...) -> None: ...

class PrepareResponse(_message.Message):
    __slots__ = ("vote_commit", "message")
    VOTE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    vote_commit: bool
    message: str
    def __init__(self, vote_commit: bool = ..., message: _Optional[str] = ...) -> None: ...

class CommitRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class CommitResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...

class AbortRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class AbortResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...
