"""Connect a shop product to the dropship supplier (or disconnect it)."""
from __future__ import annotations

from .models import SupplierProduct


def link_product(product, supplier_product_id: str, *, price=None) -> SupplierProduct:
    """Make `product` a dropship product. If the supplier's real `product_id` differs from the id first used (a CSV upload only knows
    the numeric id), the next 'Import from supplier' corrects it by matching the numeric system id."""
    link = SupplierProduct.objects.filter(product=product).first()
    if link is None:
        return SupplierProduct.objects.create(product=product, supplier_product_id=supplier_product_id[:40], final_price=price)
    if link.supplier_product_id != supplier_product_id[:40]:
        link.supplier_product_id = supplier_product_id[:40]
        link.save()
    return link


def unlink_product(product) -> None:
    SupplierProduct.objects.filter(product=product).delete()
