import unittest

from actions import energy_actions


class EnergyActionsTests(unittest.TestCase):
    def test_calculate_consumption_for_multiple_devices(self):
        result = energy_actions.calculate_consumption(
            quantity=12,
            power_watts=60,
            duration_hours=8,
        )

        self.assertAlmostEqual(5.76, result["energy_kwh"], places=2)

    def test_calculate_energy_cost(self):
        result = energy_actions.calculate_energy_cost(energy_kwh=4, price_per_kwh=0.15)

        self.assertAlmostEqual(0.6, result["total_cost"], places=2)

    def test_solar_sizing_recommends_power(self):
        result = energy_actions.calculate_solar_sizing(
            daily_energy_kwh=2,
            sun_hours=5,
            system_efficiency=0.8,
        )

        self.assertGreater(result["recommended_pv_watts"], 0)

    def test_battery_sizing_recommends_capacity(self):
        result = energy_actions.calculate_battery_sizing(
            energy_kwh=3,
            system_voltage=24,
            depth_of_discharge=0.6,
            autonomy_days=2,
        )

        self.assertAlmostEqual(416.67, result["recommended_capacity_ah"], places=2)

    def test_inverter_sizing_applies_margin(self):
        result = energy_actions.calculate_inverter_sizing(
            total_power_watts=3200,
            safety_margin=0.3,
        )

        self.assertAlmostEqual(4160, result["recommended_inverter_watts"], places=2)

    def test_audit_template_includes_requested_topic(self):
        result = energy_actions.generate_audit_template("hotel")

        self.assertIn("hotel", result.lower())
        self.assertIn("Actions | Priorite", result)


if __name__ == "__main__":
    unittest.main()
