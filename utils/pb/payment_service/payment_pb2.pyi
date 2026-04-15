from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class PaymentPrepareRequest(_message.Message):
    __slots__ = ("order_id", "amount", "user_name")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    AMOUNT_FIELD_NUMBER: _ClassVar[int]
    USER_NAME_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    amount: float
    user_name: str
    def __init__(self, order_id: _Optional[str] = ..., amount: _Optional[float] = ..., user_name: _Optional[str] = ...) -> None: ...

class PaymentPrepareResponse(_message.Message):
    __slots__ = ("vote_commit", "message")
    VOTE_COMMIT_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    vote_commit: bool
    message: str
    def __init__(self, vote_commit: bool = ..., message: _Optional[str] = ...) -> None: ...

class PaymentCommitRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class PaymentCommitResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...

class PaymentAbortRequest(_message.Message):
    __slots__ = ("order_id",)
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    def __init__(self, order_id: _Optional[str] = ...) -> None: ...

class PaymentAbortResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...
