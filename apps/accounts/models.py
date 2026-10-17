from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.conf import settings
from django.db import models
from django.db.models import Q


class UserManager(BaseUserManager):
    use_in_migrations = True

    def get_by_natural_key(self, username):
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("email_verified", True)
        return self._create(email, password, **extra)


class User(AbstractUser):
    username = None
    email = models.EmailField("email address", unique=True)
    phone = models.CharField(max_length=20, blank=True)
    email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    two_factor_enabled = models.BooleanField("email sign-in code (2FA)", default=False)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def requires_two_factor(self) -> bool:
        """Staff always need the emailed code; customers opt in from their security page."""
        return self.is_staff or self.is_superuser or self.two_factor_enabled

    @property
    def display_name(self) -> str:
        return self.first_name or self.email.split("@")[0]


class DeliveryArea(models.TextChoices):
    DHAKA = "dhaka", "Inside Dhaka"
    OUTSIDE = "outside", "Outside Dhaka"


class AddressBase(models.Model):
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=20)
    address = models.CharField("street address", max_length=255)
    city = models.CharField("city / district", max_length=80)
    delivery_area = models.CharField(max_length=10, choices=DeliveryArea.choices, default=DeliveryArea.DHAKA)

    class Meta:
        abstract = True

    def as_data(self) -> dict:
        return {"full_name": self.full_name, "phone": self.phone, "address": self.address, "city": self.city, "delivery_area": self.delivery_area}

    @property
    def one_line(self) -> str:
        return f"{self.address}, {self.city}"


class PresentAddress(AddressBase):
    """Where the customer lives. One per user; used as billing/invoice details."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="present_address")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "present addresses"

    def __str__(self) -> str:
        return f"Present: {self.one_line}"


class ShippingAddress(AddressBase):
    """Delivery destinations. Many per user, exactly one default."""

    class Label(models.TextChoices):
        HOME = "home", "Home"
        OFFICE = "office", "Office"
        OTHER = "other", "Other"

    MAX_PER_USER = 10

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shipping_addresses")
    label = models.CharField(max_length=10, choices=Label.choices, default=Label.HOME)
    notes = models.CharField("delivery notes", max_length=255, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-is_default", "-created_at", "-id")
        verbose_name_plural = "shipping addresses"
        constraints = [
            models.UniqueConstraint(fields=["user"], condition=Q(is_default=True), name="one_default_shipping_per_user"),
        ]

    def __str__(self) -> str:
        return f"{self.get_label_display()}: {self.one_line}"

    def as_data(self) -> dict:
        return {**super().as_data(), "notes": self.notes}


class LoginCode(models.Model):
    """Emailed one-time code for the second sign-in step. Stored hashed, short-lived, attempt-limited."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="login_codes")
    code_hash = models.CharField(max_length=64, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    resends = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]
