from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class OrderItem(_message.Message):
    __slots__ = ("title", "quantity")
    TITLE_FIELD_NUMBER: _ClassVar[int]
    QUANTITY_FIELD_NUMBER: _ClassVar[int]
    title: str
    quantity: int
    def __init__(self, title: _Optional[str] = ..., quantity: _Optional[int] = ...) -> None: ...

class OrderData(_message.Message):
    __slots__ = ("order_id", "user_name", "user_contact", "card_number", "expiration_date", "cvv", "item_count", "terms_accepted", "items")
    ORDER_ID_FIELD_NUMBER: _ClassVar[int]
    USER_NAME_FIELD_NUMBER: _ClassVar[int]
    USER_CONTACT_FIELD_NUMBER: _ClassVar[int]
    CARD_NUMBER_FIELD_NUMBER: _ClassVar[int]
    EXPIRATION_DATE_FIELD_NUMBER: _ClassVar[int]
    CVV_FIELD_NUMBER: _ClassVar[int]
    ITEM_COUNT_FIELD_NUMBER: _ClassVar[int]
    TERMS_ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    ITEMS_FIELD_NUMBER: _ClassVar[int]
    order_id: str
    user_name: str
    user_contact: str
    card_number: str
    expiration_date: str
    cvv: str
    item_count: int
    terms_accepted: bool
    items: _containers.RepeatedCompositeFieldContainer[OrderItem]
    def __init__(self, order_id: _Optional[str] = ..., user_name: _Optional[str] = ..., user_contact: _Optional[str] = ..., card_number: _Optional[str] = ..., expiration_date: _Optional[str] = ..., cvv: _Optional[str] = ..., item_count: _Optional[int] = ..., terms_accepted: bool = ..., items: _Optional[_Iterable[_Union[OrderItem, _Mapping]]] = ...) -> None: ...

class EnqueueRequest(_message.Message):
    __slots__ = ("order",)
    ORDER_FIELD_NUMBER: _ClassVar[int]
    order: OrderData
    def __init__(self, order: _Optional[_Union[OrderData, _Mapping]] = ...) -> None: ...

class DequeueRequest(_message.Message):
    __slots__ = ("executor_id",)
    EXECUTOR_ID_FIELD_NUMBER: _ClassVar[int]
    executor_id: str
    def __init__(self, executor_id: _Optional[str] = ...) -> None: ...

class QueueResponse(_message.Message):
    __slots__ = ("success", "message")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    def __init__(self, success: bool = ..., message: _Optional[str] = ...) -> None: ...

class DequeueResponse(_message.Message):
    __slots__ = ("success", "message", "order")
    SUCCESS_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    ORDER_FIELD_NUMBER: _ClassVar[int]
    success: bool
    message: str
    order: OrderData
    def __init__(self, success: bool = ..., message: _Optional[str] = ..., order: _Optional[_Union[OrderData, _Mapping]] = ...) -> None: ...
