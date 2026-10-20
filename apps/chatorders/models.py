from django.db import models


class ChatOrder(models.Model):
    """Marks an order that was taken in a chat (by the n8n bot). It is always created Pending: staff phone the customer and confirm."""

    class Channel(models.TextChoices):
        MESSENGER = "messenger", "Messenger"
        WHATSAPP = "whatsapp", "WhatsApp"
        TELEGRAM = "telegram", "Telegram"

    order = models.OneToOneField("orders.Order", on_delete=models.CASCADE, related_name="chat_order")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    channel_user_id = models.CharField(max_length=80, blank=True, help_text="The customer's id in that app (for replying)")
    contact_email = models.EmailField(blank=True, help_text="Optional email the customer gave in the chat (status updates go here)")
    request_id = models.CharField(max_length=64, unique=True, help_text="Stops the same chat message from creating two orders")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-id",)

    def __str__(self) -> str:
        return f"{self.get_channel_display()} → {self.order.number}"
