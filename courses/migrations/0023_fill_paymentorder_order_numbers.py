from django.db import migrations


def fill_order_numbers(apps, schema_editor):
    PaymentOrder = apps.get_model("courses", "PaymentOrder")
    for index, order in enumerate(PaymentOrder.objects.order_by("created_at", "id")):
        PaymentOrder.objects.filter(pk=order.pk).update(order_number=index)


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0022_paymentorder_order_number"),
    ]

    operations = [
        migrations.RunPython(fill_order_numbers, migrations.RunPython.noop),
    ]
