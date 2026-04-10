import re

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.contrib.auth.models import Group


User = get_user_model()


class RegistrationForm(forms.ModelForm):
    password1 = forms.CharField(
        label="Пароль",
        widget=forms.PasswordInput,
        required=True,
    )
    password2 = forms.CharField(
        label="Пароль еще раз",
        widget=forms.PasswordInput,
        required=True,
    )

    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email", "tariff_code")
        labels = {
            "username": "Логин",
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": "Электронная почта",
            "tariff_code": "Тариф",
        }

    tariff_code = forms.ModelChoiceField(
        required=True,
        queryset=Group.objects.filter(profile__isnull=False, profile__is_admin_group=False).order_by("name"),
        label="Тариф",
        empty_label=None,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tariff_code"].label_from_instance = self._tariff_label

    @staticmethod
    def _tariff_label(group: Group):
        if hasattr(group, "profile"):
            label = group.profile.public_name or group.name
            amount = getattr(group.profile, "payment_amount", 0) or 0
            currency = getattr(group.profile, "payment_currency", "") or ""
            if currency == "933":
                currency = "BYN"
            if amount > 0:
                label = f"{label} — {amount / 100:.2f} {currency}".strip()
            return label
        return group.name

    @staticmethod
    def _validate_password_rules(password: str) -> None:
        errors = []
        if len(password) < 10:
            errors.append("Пароль должен быть не короче 10 символов.")
        if not re.search(r"[A-Za-z]", password):
            errors.append("Пароль должен содержать хотя бы одну букву.")
        if not re.search(r"\d", password):
            errors.append("Пароль должен содержать хотя бы одну цифру.")
        if not re.search(r"[^A-Za-z0-9]", password):
            errors.append("Пароль должен содержать хотя бы один спецсимвол.")
        if errors:
            raise ValidationError(errors)

    def clean_username(self):
        username = (self.cleaned_data.get("username") or "").strip()
        field = User._meta.get_field("username")
        for validator in field.validators:
            validator(username)
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError("Такой логин уже используется.")
        return username

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1") or ""
        password2 = self.cleaned_data.get("password2") or ""
        if password1 and password2 and password1 != password2:
            raise ValidationError("Пароли не совпадают.")
        self._validate_password_rules(password1)
        return password2

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
            tariff_group = self.cleaned_data.get("tariff_code")
            if tariff_group:
                user.groups.add(tariff_group)
        return user
