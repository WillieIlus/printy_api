from accounts.services.roles import ActorRole, get_actor_role


def select_actor_serializer(model_name, user, default=None, instance=None):
    role = get_actor_role(user)

    if model_name == "quote":
        from quotes.quote_actor_serializers import (
            QuoteAdminSerializer,
            QuoteBrokerSerializer,
            QuoteClientSerializer,
            QuoteShopSerializer,
        )

        mapping = {
            ActorRole.CLIENT: QuoteClientSerializer,
            ActorRole.BROKER: QuoteBrokerSerializer,
            ActorRole.MANAGER: QuoteBrokerSerializer,
            ActorRole.SHOP: QuoteShopSerializer,
            ActorRole.ADMIN: QuoteAdminSerializer,
        }
    elif model_name == "quote_request":
        from quotes.quote_request_actor_serializers import (
            QuoteRequestAdminSerializer,
            QuoteRequestBrokerSerializer,
            QuoteRequestClientSerializer,
            QuoteRequestShopSerializer,
        )

        mapping = {
            ActorRole.CLIENT: QuoteRequestClientSerializer,
            ActorRole.BROKER: QuoteRequestBrokerSerializer,
            ActorRole.MANAGER: QuoteRequestBrokerSerializer,
            ActorRole.SHOP: QuoteRequestShopSerializer,
            ActorRole.ADMIN: QuoteRequestAdminSerializer,
        }
    elif model_name == "financial_split":
        from quotes.financial_split_actor_serializers import (
            QuoteFinancialSplitAdminSerializer,
            QuoteFinancialSplitBrokerSerializer,
            QuoteFinancialSplitClientSerializer,
            QuoteFinancialSplitShopSerializer,
        )

        mapping = {
            ActorRole.CLIENT: QuoteFinancialSplitClientSerializer,
            ActorRole.BROKER: QuoteFinancialSplitBrokerSerializer,
            ActorRole.MANAGER: QuoteFinancialSplitBrokerSerializer,
            ActorRole.SHOP: QuoteFinancialSplitShopSerializer,
            ActorRole.ADMIN: QuoteFinancialSplitAdminSerializer,
        }
    elif model_name == "managed_job":
        from jobs.managed_job_actor_serializers import (
            ManagedJobAdminSerializer,
            ManagedJobBrokerSerializer,
            ManagedJobClientSerializer,
            ManagedJobShopSerializer,
        )
        if instance is not None and getattr(instance, "broker_id", None) == getattr(user, "id", None):
            return ManagedJobBrokerSerializer

        mapping = {
            ActorRole.CLIENT: ManagedJobClientSerializer,
            ActorRole.BROKER: ManagedJobBrokerSerializer,
            ActorRole.MANAGER: ManagedJobBrokerSerializer,
            ActorRole.SHOP: ManagedJobShopSerializer,
            ActorRole.ADMIN: ManagedJobAdminSerializer,
        }
    elif model_name == "payment":
        from payments.payment_actor_serializers import (
            PaymentAdminSerializer,
            PaymentBrokerSerializer,
            PaymentClientSerializer,
            PaymentShopSerializer,
        )

        mapping = {
            ActorRole.CLIENT: PaymentClientSerializer,
            ActorRole.BROKER: PaymentBrokerSerializer,
            ActorRole.MANAGER: PaymentBrokerSerializer,
            ActorRole.SHOP: PaymentShopSerializer,
            ActorRole.ADMIN: PaymentAdminSerializer,
        }
    else:
        raise ValueError(f"Unknown model: {model_name}")

    if role and role in mapping:
        return mapping[role]
    return default
