def calculate_consumption(quantity, power_watts, duration_hours):
    total_power_watts = quantity * power_watts
    energy_kwh = (total_power_watts * duration_hours) / 1000
    return {
        "quantity": quantity,
        "power_watts": power_watts,
        "duration_hours": duration_hours,
        "total_power_watts": total_power_watts,
        "energy_kwh": energy_kwh,
    }


def calculate_energy_cost(energy_kwh, price_per_kwh):
    total_cost = energy_kwh * price_per_kwh
    return {
        "energy_kwh": energy_kwh,
        "price_per_kwh": price_per_kwh,
        "total_cost": total_cost,
    }


def calculate_solar_sizing(daily_energy_kwh, sun_hours=5, system_efficiency=0.8):
    daily_energy_wh = daily_energy_kwh * 1000
    recommended_pv_watts = daily_energy_wh / max(sun_hours * system_efficiency, 0.01)
    return {
        "daily_energy_kwh": daily_energy_kwh,
        "daily_energy_wh": daily_energy_wh,
        "sun_hours": sun_hours,
        "system_efficiency": system_efficiency,
        "recommended_pv_watts": recommended_pv_watts,
    }


def calculate_battery_sizing(
    energy_kwh,
    system_voltage=12,
    depth_of_discharge=0.5,
    autonomy_days=1,
):
    required_wh = energy_kwh * 1000 * autonomy_days
    recommended_capacity_ah = required_wh / max(system_voltage * depth_of_discharge, 0.01)
    return {
        "energy_kwh": energy_kwh,
        "system_voltage": system_voltage,
        "depth_of_discharge": depth_of_discharge,
        "autonomy_days": autonomy_days,
        "recommended_capacity_ah": recommended_capacity_ah,
    }


def calculate_inverter_sizing(total_power_watts, safety_margin=0.25):
    recommended_inverter_watts = total_power_watts * (1 + safety_margin)
    return {
        "total_power_watts": total_power_watts,
        "safety_margin": safety_margin,
        "recommended_inverter_watts": recommended_inverter_watts,
    }


def generate_audit_template(topic):
    return (
        f"Fiche d'audit energetique - {topic}\n"
        "Actions | Priorite | Delai | Investissement | Economie estimee\n"
        "Mesure 1 | Haute | 30 jours | A estimer | A estimer\n"
        "Mesure 2 | Moyenne | 60 jours | A estimer | A estimer"
    )


def describe_formulae():
    return (
        "Consommation: Energie (kWh) = Puissance (W) x Duree (h) / 1000.\n"
        "Cout: Cout = Energie (kWh) x Prix du kWh.\n"
        "Solaire: Puissance PV = Energie journaliere (Wh) / (heures de soleil x rendement).\n"
        "Batterie: Capacite (Ah) = Energie (Wh) / (Tension x DOD).\n"
        "Onduleur: Puissance recommandee = somme des charges x marge de securite."
    )
