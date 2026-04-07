import json
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

from django.contrib.auth.models import Group, User
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from .models import PaymentOrder, UserAccess


PAYMENT_REGISTER_URL = "https://abby.rbsuat.com/payment/rest/register.do"
PAYMENT_STATUS_URL = "https://abby.rbsuat.com/payment/rest/getOrderStatusExtended.do"


class PaymentGatewayError(Exception):
    pass


def _build_absolute_url(request, raw_url: str) -> str:
    return request.build_absolute_uri(reverse("courses:payment_return"))


def _append_query_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[key] = value
    return urlunparse(parsed._replace(query=urlencode(query)))


def _masked_request_payload(payload: dict) -> dict:
    masked = dict(payload)
    if masked.get("password"):
        masked["password"] = "********"
    return masked


def _profile_for_group(tariff_group: Group):
    profile = getattr(tariff_group, "profile", None)
    if not profile:
        raise PaymentGatewayError("Для выбранного тарифа не найден профиль настройки оплаты.")
    return profile


def _validate_payment_profile(profile) -> None:
    missing = []
    if not profile.payment_amount:
        missing.append("amount")
    if not profile.payment_currency:
        missing.append("currency")
    if not profile.payment_user_name:
        missing.append("userName")
    if not profile.payment_password:
        missing.append("password")
    if not profile.payment_return_url:
        missing.append("returnUrl")
    if not profile.payment_language:
        missing.append("language")
    if missing:
        raise PaymentGatewayError(
            f"Для выбранного тарифа не настроены поля оплаты: {', '.join(missing)}."
        )


def register_payment_order(request, user: User, tariff_group: Group) -> PaymentOrder:
    profile = _profile_for_group(tariff_group)
    _validate_payment_profile(profile)

    last_order_number = PaymentOrder.objects.aggregate(max_number=Max("order_number")).get("max_number")
    next_order_number = (last_order_number + 10) if last_order_number is not None else 0

    order = PaymentOrder.objects.create(
        order_number=next_order_number,
        user=user,
        tariff_group=tariff_group,
        amount=profile.payment_amount,
        currency=profile.payment_currency,
        description=profile.payment_description or f"Оплата тарифа {profile.public_name}",
        register_url=PAYMENT_REGISTER_URL,
        return_url="",
    )

    final_return_url = _append_query_param(
        _build_absolute_url(request, profile.payment_return_url),
        "payment",
        str(order.public_id),
    )

    payload = {
        "orderNumber": str(order.order_number),
        "amount": str(profile.payment_amount),
        "currency": profile.payment_currency,
        "userName": profile.payment_user_name,
        "password": profile.payment_password,
        "returnUrl": final_return_url,
        "description": profile.payment_description or f"Оплата тарифа {profile.public_name}",
        "language": profile.payment_language,
    }

    order.return_url = final_return_url
    order.request_payload = _masked_request_payload(payload)

    request_data = urlencode(payload).encode("utf-8")
    request_obj = Request(
        PAYMENT_REGISTER_URL,
        data=request_data,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request_obj, timeout=20) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        order.status = PaymentOrder.STATUS_ERROR
        order.error_message = body or str(exc)
        order.save(update_fields=["status", "error_message", "request_payload", "return_url"])
        raise PaymentGatewayError("Платежный шлюз отклонил регистрацию заказа.")
    except URLError as exc:
        order.status = PaymentOrder.STATUS_ERROR
        order.error_message = str(exc)
        order.save(update_fields=["status", "error_message", "request_payload", "return_url"])
        raise PaymentGatewayError("Не удалось связаться с платежным шлюзом.")

    try:
        response_data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        order.status = PaymentOrder.STATUS_ERROR
        order.error_message = raw_body
        order.save(update_fields=["status", "error_message", "request_payload", "return_url"])
        raise PaymentGatewayError("Платежный шлюз вернул некорректный ответ.") from exc

    order.response_payload = response_data
    order.gateway_order_id = response_data.get("orderId", "") or ""
    order.gateway_form_url = response_data.get("formUrl", "") or ""

    if response_data.get("errorCode"):
        order.status = PaymentOrder.STATUS_ERROR
        order.error_message = response_data.get("errorMessage", "Неизвестная ошибка платежного шлюза.")
        order.save(
            update_fields=[
                "status",
                "error_message",
                "request_payload",
                "response_payload",
                "gateway_order_id",
                "gateway_form_url",
                "return_url",
            ]
        )
        raise PaymentGatewayError(order.error_message)

    if not order.gateway_order_id or not order.gateway_form_url:
        order.status = PaymentOrder.STATUS_ERROR
        order.error_message = "Платежный шлюз не вернул orderId или formUrl."
        order.save(
            update_fields=[
                "status",
                "error_message",
                "request_payload",
                "response_payload",
                "gateway_order_id",
                "gateway_form_url",
                "return_url",
            ]
        )
        raise PaymentGatewayError(order.error_message)

    order.save(
        update_fields=[
            "request_payload",
            "response_payload",
            "gateway_order_id",
            "gateway_form_url",
            "return_url",
        ]
    )
    return order


def find_payment_order_for_return(request) -> PaymentOrder | None:
    payment_public_id = (request.GET.get("payment") or "").strip()
    gateway_order_id = (request.GET.get("orderId") or request.GET.get("mdOrder") or "").strip()

    if payment_public_id:
        return PaymentOrder.objects.select_related("user", "tariff_group", "tariff_group__profile").filter(
            public_id=payment_public_id
        ).first()

    if gateway_order_id:
        return PaymentOrder.objects.select_related("user", "tariff_group", "tariff_group__profile").filter(
            gateway_order_id=gateway_order_id
        ).first()

    return None


def activate_payment_order(order: PaymentOrder) -> PaymentOrder:
    if order.status == PaymentOrder.STATUS_PAID:
        return order

    now = timezone.now()
    profile = getattr(order.tariff_group, "profile", None)
    if not profile:
        raise PaymentGatewayError("Не найден профиль тарифа для активации доступа.")

    user_access, _ = UserAccess.objects.get_or_create(user=order.user)
    user_access.paid = True
    user_access.access_start_at = now
    if profile.access_duration_days:
        user_access.access_end_at = now + timedelta(days=profile.access_duration_days)
    else:
        user_access.access_end_at = None
    user_access.save(update_fields=["paid", "access_start_at", "access_end_at"])

    order.status = PaymentOrder.STATUS_PAID
    order.paid_at = now
    order.error_message = ""
    order.save(update_fields=["status", "paid_at", "error_message"])
    return order


def get_payment_order_status(order: PaymentOrder) -> tuple[int | None, dict]:
    profile = _profile_for_group(order.tariff_group)
    if not order.gateway_order_id:
        raise PaymentGatewayError("У заказа отсутствует orderId платежного шлюза.")
    if not profile.payment_user_name or not profile.payment_password:
        raise PaymentGatewayError("Для тарифа не настроены userName/password для проверки статуса оплаты.")

    payload = {
        "orderId": order.gateway_order_id,
        "userName": profile.payment_user_name,
        "password": profile.payment_password,
        "language": profile.payment_language or "ru",
    }
    request_data = urlencode(payload).encode("utf-8")
    request_obj = Request(
        PAYMENT_STATUS_URL,
        data=request_data,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urlopen(request_obj, timeout=20) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise PaymentGatewayError(body or "Платежный шлюз отклонил запрос статуса оплаты.")
    except URLError as exc:
        raise PaymentGatewayError("Не удалось получить статус оплаты у платежного шлюза.") from exc

    try:
        response_data = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise PaymentGatewayError("Платежный шлюз вернул некорректный ответ статуса.") from exc

    error_code = response_data.get("errorCode")
    if error_code not in (None, "", 0, "0"):
        raise PaymentGatewayError(response_data.get("errorMessage", "Платежный шлюз вернул ошибку статуса."))

    raw_status = response_data.get("orderStatus")
    try:
        order_status = int(raw_status) if raw_status is not None else None
    except (TypeError, ValueError):
        order_status = None

    return order_status, response_data
