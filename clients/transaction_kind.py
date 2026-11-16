"""Conservative classifications from structured bank data, never merchant text."""
PURCHASE_GROUPS = {
    'FOOD_AND_DRINK', 'GENERAL_MERCHANDISE', 'GENERAL_SERVICES',
    'ENTERTAINMENT', 'MEDICAL', 'PERSONAL_CARE', 'HOME_IMPROVEMENT',
    'TRANSPORTATION', 'TRAVEL', 'RENT_AND_UTILITIES',
}


def transaction_kind(tx):
    category = getattr(tx, 'personal_finance_category', None)
    if not category:
        return 'unclassified'
    def field(name):
        return category.get(name) if isinstance(category, dict) else getattr(category, name, None)
    if field('confidence_level') not in ('HIGH', 'VERY_HIGH'):
        return 'unclassified'
    primary = field('primary')
    if primary in ('TRANSFER_IN', 'TRANSFER_OUT'):
        return 'transfer'
    if primary == 'LOAN_PAYMENTS':
        return 'payment'
    if primary in PURCHASE_GROUPS and tx.amount > 0:
        return 'purchase'
    # Negative amounts could be refunds, rewards, income, or payments. Keep
    # them visible for review until an explicit Sheet override identifies them.
    return 'unclassified'
