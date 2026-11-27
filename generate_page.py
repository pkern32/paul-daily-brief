def parse_price_number(val):
    cleaned = re.sub(r"[^\d.]", "", str(val or ""))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def estimate_monthly_tax(listing, price):
    """
    Uses actual annual property taxes paid on the listing when provided;
    otherwise estimates from county/ZIP benchmarks near Loveland (45140).
    """
    actual_annual_tax = parse_price_number(listing.get("annual_tax_paid"))
    if actual_annual_tax > 0:
        return actual_annual_tax / 12.0, True

    pitch = str(listing.get("metrics_pitch") or "").lower()
    address = str(listing.get("address") or "").lower()
    combined = f"{address} {pitch}"

    if "warren" in combined or "lebanon" in combined or "mason" in combined:
        est_rate = 0.0122  # Warren County benchmark
    elif "clermont" in combined or "milford" in combined:
        est_rate = 0.0145  # Clermont / Loveland east benchmark
    else:
        est_rate = 0.0165  # Loveland / Hamilton County benchmark

    return (price * est_rate) / 12.0, False


def estimate_monthly_cash_flow(listing, is_multifamily=False, down_payment_pct=0.20, annual_rate=0.073):
    """
    Estimates monthly cash flow using actual annual property taxes paid when available,
    plus separate monthly allowances for insurance, vacancy, repairs/CapEx, and management.
    """
    price = parse_price_number(listing.get("price"))
    if price <= 0:
        return "Est. Cash Flow: N/A"

    pitch = str(listing.get("metrics_pitch") or "").lower()
    strategy = str(listing.get("strategy") or "").lower()

    if is_multifamily:
        unit_match = re.search(r"(\d+)\s*-?\s*unit", pitch)
        units = int(unit_match.group(1)) if unit_match else (
            4 if "quadplex" in strategy else 3 if "triplex" in strategy else 2
        )
        per_unit_rent = 1650.0 if units == 2 else 1550.0
        gross_rent = units * per_unit_rent
        vacancy_rate = 0.06
        non_tax_opex_rate = 0.24  # insurance + maintenance/CapEx + management (excluding tax)
    else:
        bed_match = re.search(r"(\d+)\s*(?:bd|bed|br)", pitch)
        beds = int(bed_match.group(1)) if bed_match else 3
        base_ltr = 1850.0 if beds <= 2 else (2160.0 if beds == 3 else 2750.0)
        if "short-term" in strategy or "airbnb" in strategy or "str" in pitch:
            gross_rent = base_ltr * 1.45
            vacancy_rate = 0.15
            non_tax_opex_rate = 0.28
        else:
            gross_rent = base_ltr
            vacancy_rate = 0.05
            non_tax_opex_rate = 0.22

    monthly_tax, is_actual_tax = estimate_monthly_tax(listing, price)
    effective_rent = gross_rent * (1.0 - vacancy_rate)
    other_opex = gross_rent * non_tax_opex_rate

    loan_amount = price * (1.0 - down_payment_pct)
    monthly_rate = annual_rate / 12.0
    n_payments = 360
    if monthly_rate > 0:
        pi = loan_amount * (monthly_rate * (1 + monthly_rate) ** n_payments) / (
            (1 + monthly_rate) ** n_payments - 1
        )
    else:
        pi = loan_amount / n_payments

    cash_flow = effective_rent - monthly_tax - other_opex - pi
    sign = "+" if cash_flow >= 0 else "-"
    tax_label = f"Actual Tax ${round(monthly_tax):,}/mo" if is_actual_tax else f"Est. Tax ${round(monthly_tax):,}/mo"
    return f"Est. Cash Flow: {sign}${abs(round(cash_flow)):,}/mo ({tax_label})"
