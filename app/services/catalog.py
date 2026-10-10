from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Category, Product, Shop

DEFAULT_CATEGORIES = [
    ("Fashion & Clothing", "ፋሽን እና ልብስ"), ("Electronics", "ኤሌክትሮኒክስ"),
    ("Phones & Accessories", "ስልክ እና መለዋወጫ"), ("Home & Furniture", "ቤት እና የቤት ዕቃ"),
    ("Beauty & Health", "ውበት እና ጤና"), ("Food & Groceries", "ምግብ እና ሸቀጣ ሸቀጥ"),
    ("Books & Stationery", "መጻሕፍት እና የጽህፈት መሳሪያ"), ("Kids & Toys", "ልጆች እና መጫወቻ"),
    ("Vehicles & Parts", "ተሽከርካሪ እና መለዋወጫ"), ("Other", "ሌላ"),
]


async def seed_categories(session: AsyncSession) -> None:
    if (await session.execute(select(Category.id).limit(1))).first():
        return
    session.add_all(Category(name_en=en, name_am=am) for en, am in DEFAULT_CATEGORIES)


async def list_categories(session: AsyncSession):
    return (await session.scalars(select(Category).order_by(Category.id))).all()


def cat_name(c: Category, lang: str | None) -> str:
    return c.name_am if lang == "am" else c.name_en


async def list_active_shops(session: AsyncSession):
    return (await session.scalars(select(Shop).where(Shop.status == "active").order_by(Shop.name))).all()


async def get_shop(session: AsyncSession, shop_id: int):
    return await session.get(Shop, shop_id)


async def get_shop_by_slug(session: AsyncSession, slug: str):
    return await session.scalar(select(Shop).where(Shop.slug == slug))


async def get_shop_by_owner(session: AsyncSession, user_id: int):
    return await session.scalar(select(Shop).where(Shop.owner_id == user_id))


async def list_products(session: AsyncSession, shop_id: int, category_id: int | None = None,
                        include_hidden: bool = False, limit: int = 30):
    q = select(Product).where(Product.shop_id == shop_id)
    if not include_hidden:
        q = q.where(Product.status == "active")
    if category_id:
        q = q.where(Product.category_id == category_id)
    return (await session.scalars(q.order_by(Product.id.desc()).limit(limit))).all()


async def get_product(session: AsyncSession, product_id: int):
    return await session.get(Product, product_id)
