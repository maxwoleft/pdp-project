"""Імпорт всіх моделей для реєстрації в metadata."""
from app.infrastructure.db.models.common import Country, CountryMessenger  # noqa: F401
from app.infrastructure.db.models.catalog import (  # noqa: F401
    Category,
    Position,
    Service,
    Product,
    ProductCategory,
)
from app.infrastructure.db.models.staff import (  # noqa: F401
    Salon,
    Employee,
    EmployeePosition,
)
from app.infrastructure.db.models.scheduling import (  # noqa: F401
    TimeSlot,
    Client,
    Booking,
    BookingStatus,
    ProductOrder,
)
from app.infrastructure.db.models.eval import (  # noqa: F401
    AdminUser,
    EvalScenario,
)
from app.infrastructure.db.models.profile import (  # noqa: F401
    ServiceProfile,
    ServiceProfileOverride,
    ServiceProfileTranslation,
    ServiceProfileVariant,
    ServiceProfileVariantEvent,
    ServiceProfileVersion,
)
from app.infrastructure.db.models.category_group import (  # noqa: F401
    CategoryGroup,
    GroupMember,
)
