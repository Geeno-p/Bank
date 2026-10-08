from pydantic import Field

from regintel.schemas.common import Base, RiskBand


class BankEntity(Base):
    id: str
    name: str
    type: str
    geography: str


class Product(Base):
    id: str
    name: str
    line: str
    inherent_risk: RiskBand


class CustomerBase(Base):
    total: int
    risk_distribution: dict[str, int]


class Vendor(Base):
    id: str
    name: str
    service: str
    material: bool


class BankProfile(Base):
    id: str
    name: str
    licence_type: str
    regulators: list[str]
    jurisdictions: list[str]
    entities: list[BankEntity]
    products: list[Product]
    customer_base: CustomerBase
    attributes: dict[str, bool] = Field(default_factory=dict)
    outsourcing_vendors: list[Vendor] = Field(default_factory=list)
    risk_appetite: dict[str, str | int | float] = Field(default_factory=dict)

    def has(self, attribute: str) -> bool:
        return self.attributes.get(attribute, False)