import math

import numpy

# Try to keep imports matching order of SAP document
from .constants import DAYS_PER_MONTH, SUMMER_MONTHS
from .utils import monthly_to_annual
from .ventilation import ventilation
from .domestic_hot_water import hot_water_use
from .solar import solar
from .fuel_use import fuel_use
from .appendix import appendix_m


def geometry(dwelling):
    if not dwelling.get('Aglazing'):
        dwelling.Aglazing = dwelling.GFA * dwelling.glazing_ratio
        dwelling.Aglazing_front = dwelling.glazing_asymmetry * \
                                  dwelling.Aglazing
        dwelling.Aglazing_back = (
                                     1. - dwelling.glazing_asymmetry) * dwelling.Aglazing
        dwelling.Aglazing_left = 0
        dwelling.Aglazing_right = 0

    elif not dwelling.get('Aglazing_front'):
        dwelling.Aglazing_front = dwelling.Aglazing / 2
        dwelling.Aglazing_back = dwelling.Aglazing / 2
        dwelling.Aglazing_left = 0
        dwelling.Aglazing_right = 0

    if dwelling.get('hlp') is not None:
        return

    if dwelling.get('aspect_ratio') is not None:
        # This is for converting for the parametric SAP style
        # dimensions to the calculation dimensions
        width = math.sqrt(dwelling.GFA / dwelling.Nstoreys / dwelling.aspect_ratio)

        depth = math.sqrt(dwelling.GFA / dwelling.Nstoreys * dwelling.aspect_ratio)

        dwelling.volume = width * depth * (dwelling.room_height * dwelling.Nstoreys +
                                           dwelling.internal_floor_depth * (dwelling.Nstoreys - 1))

        dwelling.Aextwall = 2 * (dwelling.room_height * dwelling.Nstoreys + dwelling.internal_floor_depth * (
            dwelling.Nstoreys - 1)) * (width + depth * (1 - dwelling.terrace_level)) - dwelling.Aglazing

        dwelling.Apartywall = 2 * (dwelling.room_height * dwelling.Nstoreys +
                                   dwelling.internal_floor_depth *
                                   (dwelling.Nstoreys - 1)) * (depth * dwelling.terrace_level)

        if dwelling.type == "House":
            dwelling.Aroof = width * depth
            dwelling.Agndfloor = width * depth
        elif dwelling.type == "MidFlat":
            dwelling.Aroof = 0
            dwelling.Agndfloor = 0
        else:
            raise RuntimeError('Unknown dwelling type: %s' % (dwelling.type,))

    else:
        if not dwelling.get('volume'):
            dwelling.volume = dwelling.GFA * dwelling.storey_height

        if not dwelling.get('Aextwall'):
            if dwelling.get('wall_ratio') is not None:
                dwelling.Aextwall = dwelling.GFA * dwelling.wall_ratio
            else:
                dwelling_height = dwelling.storey_height * dwelling.Nstoreys
                total_wall_A = dwelling_height * dwelling.average_perimeter
                if dwelling.get('Apartywall') is not None:
                    dwelling.Aextwall = total_wall_A - dwelling.Apartywall
                elif dwelling.get('party_wall_fraction') is not None:
                    dwelling.Aextwall = total_wall_A * (
                        1 - dwelling.party_wall_fraction)
                else:
                    dwelling.Aextwall = total_wall_A - \
                                        dwelling.party_wall_ratio * dwelling.GFA

        if not dwelling.get('Apartywall'):
            if dwelling.get('party_wall_ratio') is not None:
                dwelling.Apartywall = dwelling.GFA * dwelling.party_wall_ratio
            else:
                dwelling.Apartywall = dwelling.Aextwall * \
                                      dwelling.party_wall_fraction / \
                                      (1 - dwelling.party_wall_fraction)

        if not dwelling.get('Aroof'):
            dwelling.Aroof = dwelling.GFA / dwelling.Nstoreys
            dwelling.Agndfloor = dwelling.GFA / dwelling.Nstoreys


def heat_loss(dwelling):
    """
    Set the attributes `h`, `hlp`, `h_fabric`, `h_bridging`, `h_vent`, `h_vent_annual`
    on the given dwelling object

    Args:
        dwelling:


    """
    if dwelling.get('hlp') is not None:
        # TODO: what is "h"?
        dwelling.h = dwelling.hlp * dwelling.GFA
        return

    UA = sum(e.Uvalue * e.area for e in dwelling.heat_loss_elements)
    A_bridging = sum(
            e.area for e in dwelling.heat_loss_elements if e.is_external)
    if dwelling.get("Uthermalbridges") is not None:
        h_bridging = dwelling.Uthermalbridges * A_bridging
    else:
        h_bridging = sum(x['length'] * x['y'] for x in dwelling.y_values)

    h_vent = 0.33 * dwelling.infiltration_ach * dwelling.volume

    dwelling.h = UA + h_bridging + h_vent
    dwelling.hlp = dwelling.h / dwelling.GFA

    dwelling.h_fabric = UA
    dwelling.h_bridging = h_bridging
    dwelling.h_vent = h_vent

    dwelling.h_vent_annual = monthly_to_annual(h_vent)


def fghr_savings(dwelling):
    if dwelling.fghrs['heat_store'] == 1:
        # !!! untested
        assert False
        Kfl = dwelling.fghrs['direct_useful_heat_recovered']
        return Kfl * Kn * dwelling.total_water_heating

    equation_space_heats = [e['space_heating_requirement']
                            for e in dwelling.fghrs['equations']]

    # !!! Should only use heat provided by this system
    if dwelling.water_sys is dwelling.main_sys_1:
        space_heat_frac = (dwelling.fraction_of_heat_from_main *
                           dwelling.main_heating_fraction)
    elif dwelling.water_sys is dwelling.main_sys_2:
        space_heat_frac = (dwelling.fraction_of_heat_from_main *
                           dwelling.main_heating_2_fraction)
    else:
        # !!! Not allowed to have fghrs on secondary system?
        # !!! Are you even allowed fghrs on hw only systems?
        space_heat_frac = 0

    Qspm = dwelling.Q_required * space_heat_frac

    closest_below = [max(x for x in equation_space_heats
                         if x <= Qspm[month])
                     if Qspm[month] >= min(equation_space_heats)
                     else min(equation_space_heats)
                     for month in range(12)]
    closest_above = [min(x for x in equation_space_heats
                         if x >= Qspm[month])
                     if Qspm[month] <= max(equation_space_heats)
                     else max(equation_space_heats)
                     for month in range(12)]

    closest_below_eqns = [[e for e in dwelling.fghrs['equations']
                           if e['space_heating_requirement'] == Q_req][0]
                          for Q_req in closest_below]
    closest_above_eqns = [[e for e in dwelling.fghrs['equations']
                           if e['space_heating_requirement'] == Q_req][0]
                          for Q_req in closest_above]

    # !!! For some reason solar input from FGHRS doesn't reduce Qhwm
    Qhwm = (dwelling.hw_energy_content +
            dwelling.input_from_solar -
            dwelling.savings_from_wwhrs)

    def calc_S0(equations):
        a = numpy.array([e['a'] for e in equations])
        b = numpy.array([e['b'] for e in equations])
        c = numpy.array([e['c'] for e in equations])

        res = [0, ] * 12
        for month in range(12):
            Q = min(309, max(80, Qhwm[month]))
            res[month] = (a[month] * math.log(Q) +
                          b[month] * Q +
                          c[month]) * min(1, Qhwm[month] / Q)

        return res

    S0_below = calc_S0(closest_below_eqns)
    S0_above = calc_S0(closest_above_eqns)
    S0 = [0, ] * 12
    for month in range(12):
        if closest_above[month] != closest_below[month]:
            S0[month] = S0_below[month] + (S0_above[month] - S0_below[month]) * (
                Qspm[month] - closest_below[month]) / (closest_above[month] - closest_below[month])
        else:
            S0[month] = S0_below[month]

    # !!! Should exit here for intant combi without keep hot and no
    # !!! ext store - S0 is the result

    # !!! Needs factor of 1.3 for CPSU or primary storage combi
    Vk = (dwelling.hw_cylinder_volume if dwelling.get('hw_cylinder_volume')
          else dwelling.fghrs['heat_store_total_volume'])

    if Vk >= 144:
        Kn = 0
    elif Vk >= 75:
        Kn = .48 - Vk / 300.
    elif Vk >= 15:
        Kn = 1.1925 - .77 * Vk / 60.
    else:
        Kn = 1

    Kf2 = dwelling.fghrs['direct_total_heat_recovered']
    Sm = S0 + 0.5 * Kf2 * (dwelling.storage_loss +
                           dwelling.primary_circuit_loss +
                           dwelling.combi_loss_monthly -
                           (1 - Kn) * Qhwm)

    # !!! Need to use this for combi with keep hot
    # Sm=S0+0.5*Kf2*(dwelling.combi_loss_monthly-dwelling.water_sys.keep_hot_elec_consumption)

    savings = numpy.where(Qhwm > 0,
                          Sm,
                          0)
    return savings


def water_heater_output(dwelling):
    if dwelling.get('fghrs') is not None:
        dwelling.savings_from_fghrs = fghr_savings(dwelling)
    else:
        dwelling.savings_from_fghrs = 0

    dwelling.output_from_water_heater = numpy.maximum(0,
                                                      dwelling.total_water_heating +
                                                      dwelling.input_from_solar +
                                                      dwelling.fghrs_input_from_solar  -
                                                      dwelling.savings_from_wwhrs -
                                                      dwelling.savings_from_fghrs)


def GL_sum(openings):
    return sum(0.9 * o.area * o.opening_type.frame_factor * o.opening_type.light_transmittance for o in openings)


def lighting_consumption(dwelling):
    mean_light_energy = 59.73 * (dwelling.GFA * dwelling.Nocc) ** 0.4714

    if not dwelling.get('low_energy_bulb_ratio'):
        dwelling.low_energy_bulb_ratio = int(
                100 * float(dwelling.lighting_outlets_low_energy) / dwelling.lighting_outlets_total + .5) / 100.

    C1 = 1 - 0.5 * dwelling.low_energy_bulb_ratio
    GLwin = GL_sum(o for o in dwelling.openings if not o.opening_type.roof_window and not o.opening_type.bfrc_data) * \
            dwelling.light_access_factor / dwelling.GFA
    GLroof = GL_sum(
            o for o in dwelling.openings if o.opening_type.roof_window and not o.opening_type.bfrc_data) / dwelling.GFA

    # Use frame factor of 0.7 for bfrc rated windows
    GLwin_bfrc = GL_sum(o for o in dwelling.openings if not o.opening_type.roof_window and o.opening_type.bfrc_data) * \
                 .7 * .9 * dwelling.light_access_factor / dwelling.GFA
    GLroof_bfrc = GL_sum(
            o for o in dwelling.openings if
            o.opening_type.roof_window and o.opening_type.bfrc_data) * .7 * .9 / dwelling.GFA

    GL = GLwin + GLroof + GLwin_bfrc + GLroof_bfrc
    C2 = 52.2 * GL ** 2 - 9.94 * GL + 1.433 if GL <= 0.095 else 0.96
    EL = mean_light_energy * C1 * C2
    light_consumption = EL * \
                        (1 + 0.5 * numpy.cos((2. * math.pi / 12.) * ((numpy.arange(12) + 1) - 0.2))) * \
                        DAYS_PER_MONTH / 365
    dwelling.annual_light_consumption = sum(light_consumption)
    dwelling.full_light_gain = light_consumption * \
                               (0.85 * 1000 / 24.) / DAYS_PER_MONTH

    dwelling.lighting_C1 = C1
    dwelling.lighting_GL = GL
    dwelling.lighting_C2 = C2


def internal_heat_gain(dwelling):
    dwelling.losses_gain = -40 * dwelling.Nocc
    dwelling.water_heating_gains = (
                                       1000. / 24.) * dwelling.heat_gains_from_hw / DAYS_PER_MONTH

    lighting_consumption(dwelling)

    mean_appliance_energy = 207.8 * (dwelling.GFA * dwelling.Nocc) ** 0.4714
    appliance_consumption_per_day = (mean_appliance_energy / 365.) * (
        1 + 0.157 * numpy.cos((2. * math.pi / 12.) * (numpy.arange(12) - .78)))
    dwelling.appliance_consumption = appliance_consumption_per_day * \
                                     DAYS_PER_MONTH

    if dwelling.reduced_gains:
        dwelling.met_gain = 50 * dwelling.Nocc
        dwelling.cooking_gain = 23 + 5 * dwelling.Nocc
        dwelling.appliance_gain = (
                                      0.67 * 1000. / 24) * appliance_consumption_per_day
        dwelling.light_gain = 0.4 * dwelling.full_light_gain
    else:
        dwelling.met_gain = 60 * dwelling.Nocc
        dwelling.cooking_gain = 35 + 7 * dwelling.Nocc
        dwelling.appliance_gain = (1000. / 24) * appliance_consumption_per_day
        dwelling.light_gain = dwelling.full_light_gain

    dwelling.total_internal_gains = (dwelling.met_gain
                                     + dwelling.water_heating_gains
                                     + dwelling.light_gain
                                     + dwelling.appliance_gain
                                     + dwelling.cooking_gain
                                     + dwelling.pump_gain
                                     + dwelling.losses_gain)

    if dwelling.reduced_gains:
        summer_met_gain = 60 * dwelling.Nocc
        summer_cooking_gain = 35 + 7 * dwelling.Nocc
        summer_appliance_gain = (1000. / 24) * appliance_consumption_per_day
        summer_light_gain = dwelling.full_light_gain
        dwelling.total_internal_gains_summer = (summer_met_gain
                                                + dwelling.water_heating_gains
                                                + summer_light_gain
                                                + summer_appliance_gain
                                                + summer_cooking_gain
                                                + dwelling.pump_gain
                                                + dwelling.losses_gain
                                                - dwelling.heating_system_pump_gain)
    else:
        dwelling.total_internal_gains_summer = dwelling.total_internal_gains - \
                                               dwelling.heating_system_pump_gain


def heating_requirement(dwelling):
    if not dwelling.get('thermal_mass_parameter'):
        ka = 0
        for t in dwelling.thermal_mass_elements:
            ka += t.area * t.kvalue
        dwelling.thermal_mass_parameter = ka / dwelling.GFA

    dwelling.heat_calc_results = calc_heat_required(
            dwelling, dwelling.Texternal_heating, dwelling.winter_heat_gains)
    Q_required = dwelling.heat_calc_results['heat_required']
    for i in SUMMER_MONTHS:
        Q_required[i] = 0
        dwelling.heat_calc_results['loss'][i] = 0
        dwelling.heat_calc_results['utilisation'][i] = 0
        dwelling.heat_calc_results['useful_gain'][i] = 0

    dwelling.Q_required = Q_required


def calc_heat_required(dwelling, Texternal, heat_gains):
    tau = dwelling.thermal_mass_parameter / (3.6 * dwelling.hlp)
    a = 1 + tau / 15.

    # These are for pcdf heat pumps - when heat pump is undersized it
    # can operator for longer hours on some days
    if dwelling.get('longer_heating_days'):
        N24_16_m, N24_9_m, N16_9_m = dwelling.longer_heating_days()
    else:
        N24_16_m, N24_9_m, N16_9_m = (None, None, None)

    L = dwelling.h * (dwelling.living_area_Theating - Texternal)
    util_living = heat_utilisation_factor(a, heat_gains, L)
    Tno_heat_living = temperature_no_heat(Texternal,
                                          dwelling.living_area_Theating,
                                          dwelling.heating_responsiveness,
                                          util_living,
                                          heat_gains,
                                          dwelling.h)

    Tmean_living_area = Tmean(
            Texternal, dwelling.living_area_Theating, Tno_heat_living,
            tau, dwelling.heating_control_type_sys1, N24_16_m, N24_9_m, N16_9_m, living_space=True)

    if dwelling.main_heating_fraction < 1 and dwelling.get('heating_systems_heat_separate_areas'):
        if dwelling.main_heating_fraction > dwelling.living_area_fraction:
            # both systems contribute to rest of house
            weight_1 = 1 - dwelling.main_heating_2_fraction / \
                           (1 - dwelling.living_area_fraction)

            Tmean_other_1 = temperature_rest_of_dwelling(
                    dwelling, Texternal, tau, a, L, heat_gains, dwelling.heating_control_type_sys1, N24_16_m, N24_9_m,
                    N16_9_m)
            Tmean_other_2 = temperature_rest_of_dwelling(
                    dwelling, Texternal, tau, a, L, heat_gains, dwelling.heating_control_type_sys2, N24_16_m, N24_9_m,
                    N16_9_m)

            Tmean_other = Tmean_other_1 * \
                          weight_1 + Tmean_other_2 * (1 - weight_1)
        else:
            # only sys2 does rest of house
            Tmean_other = temperature_rest_of_dwelling(
                    dwelling, Texternal, tau, a, L, heat_gains, dwelling.heating_control_type_sys2, N24_16_m, N24_9_m,
                    N16_9_m)
    else:
        Tmean_other = temperature_rest_of_dwelling(
                dwelling, Texternal, tau, a, L, heat_gains, dwelling.heating_control_type_sys1, N24_16_m, N24_9_m,
                N16_9_m)

    if not dwelling.get('living_area_fraction'):
        dwelling.living_area_fraction = dwelling.living_area / dwelling.GFA

    meanT = dwelling.living_area_fraction * Tmean_living_area + \
            (1 - dwelling.living_area_fraction) * \
            Tmean_other + dwelling.temperature_adjustment
    L = dwelling.h * (meanT - Texternal)
    utilisation = heat_utilisation_factor(a, heat_gains, L)
    return dict(
            tau=tau,
            alpha=a,
            Texternal=Texternal,
            Tmean_living_area=Tmean_living_area,
            Tmean_other=Tmean_other,
            util_living=util_living,
            Tmean=meanT,
            loss=L,
            utilisation=utilisation,
            useful_gain=utilisation * heat_gains,
            heat_required=(range_cooker_factor(dwelling) *
                           0.024 * (
                               L - utilisation * heat_gains) * DAYS_PER_MONTH),
    )


def temperature_rest_of_dwelling(dwelling, Texternal, tau, a, L, heat_gains, control_type, N24_16_m, N24_9_m, N16_9_m):
    Theat_other = heating_temperature_other_space(dwelling.hlp, control_type)
    L = dwelling.h * (Theat_other - Texternal)
    Tno_heat_other = temperature_no_heat(Texternal,
                                         Theat_other,
                                         dwelling.heating_responsiveness,
                                         heat_utilisation_factor(
                                                 a, heat_gains, L),
                                         heat_gains,
                                         dwelling.h)
    return Tmean(Texternal, Theat_other, Tno_heat_other, tau, control_type, N24_16_m, N24_9_m, N16_9_m,
                 living_space=False)


def Tmean(Texternal, Theat, Tno_heat, tau, control_type, N24_16_m, N24_9_m, N16_9_m, living_space):
    tc = 4 + 0.25 * tau
    dT = Theat - Tno_heat

    if control_type == 1 or control_type == 2 or living_space:
        # toff1=7
        # toff2=8
        # toff3=0
        # toff4=8
        # weekday
        u1 = temperature_reduction(dT, tc, 7)
        u2 = temperature_reduction(dT, tc, 8)
        Tweekday = Theat - (u1 + u2)

        # weekend
        u3 = 0  # (since Toff3=0)
        u4 = u2  # (since Toff4=Toff2)
        Tweekend = Theat - (u3 + u4)
    else:
        # toff1=9
        # toff2=8
        # toff3=9
        # toff4=8
        u1 = temperature_reduction(dT, tc, 9)
        u2 = temperature_reduction(dT, tc, 8)
        Tweekday = Theat - (u1 + u2)
        Tweekend = Tweekday

    if N24_16_m is None:
        return (5. / 7.) * Tweekday + (2. / 7.) * Tweekend
    else:
        WEm = numpy.array([9, 8, 9, 8, 9, 9, 9, 9, 8, 9, 8, 9])
        WDm = numpy.array([22, 20, 22, 22, 22, 21, 22, 22, 22, 22, 22, 22])
        return ((N24_16_m + N24_9_m) * Theat + (WEm - N24_16_m + N16_9_m) * Tweekend + (
            WDm - N16_9_m - N24_9_m) * Tweekday) / (WEm + WDm)


def temperature_reduction(delta_T, tc, time_off):
    return numpy.where(time_off <= tc,
                       (0.5 * time_off ** 2 / 24) * delta_T / tc,
                       delta_T * (time_off / 24. - (0.5 / 24.) * tc))


def temperature_no_heat(
        Texternal, Theat, responsiveness, heat_utilisation_factor,
        gains, h):
    return (1 - responsiveness) * (Theat - 2) + responsiveness * (Texternal + heat_utilisation_factor * gains / h)


def range_cooker_factor(dwelling):
    """
    Check if the main, system1 or system2 heating has a
    range cooker scaling factor and return it. If not, return 1

    :param dwelling:
    :return: the range cooker scaling factor or 1
    """
    if dwelling.get('range_cooker_heat_required_scale_factor'):
        return dwelling.range_cooker_heat_required_scale_factor

    elif dwelling.main_sys_1.get('range_cooker_heat_required_scale_factor'):
        return dwelling.main_sys_1.range_cooker_heat_required_scale_factor

    elif dwelling.get("main_sys_2") and dwelling.main_sys_2.get('range_cooker_heat_required_scale_factor'):
        return dwelling.main_sys_2.range_cooker_heat_required_scale_factor
    else:
        return 1


def cooling_requirement(dwelling):
    """
    Assign the cooling requirement to the dwelling.
    Note that this modifies the dwelling properties rather than
    returning values

    :param dwelling:
    :return:
    """
    fcool = dwelling.fraction_cooled
    if fcool == 0:
        dwelling.Q_cooling_required = numpy.array([0., ] * 12)
        return

    Texternal_summer = dwelling.external_temperature_summer
    L = dwelling.h * (dwelling.Tcooling - Texternal_summer)
    G = dwelling.summer_heat_gains

    gamma = G / L
    assert not 1 in gamma  # !!! Sort this out!

    tau = dwelling.thermal_mass_parameter / (3.6 * dwelling.hlp)
    a = 1 + tau / 15.
    utilisation = numpy.where(gamma <= 0,
                              1,
                              (1 - gamma ** -a) / (1 - gamma ** -(a + 1)))

    Qrequired = numpy.array([0., ] * 12)
    Qrequired[5:8] = (0.024 * (G - utilisation * L) * DAYS_PER_MONTH)[5:8]

    # No cooling in months where heating would be more than half of cooling
    heat_calc_results = calc_heat_required(
            dwelling, Texternal_summer, G + dwelling.heating_system_pump_gain)
    Qheat_summer = heat_calc_results['heat_required']
    Qrequired = numpy.where(3 * Qheat_summer < Qrequired,
                            Qrequired,
                            0)

    fintermittent = .25
    dwelling.Q_cooling_required = Qrequired * fcool * fintermittent


def heating_temperature_other_space(hlp, control_type):
    hlp = numpy.where(hlp < 6, hlp, 6)
    if control_type == 1:
        return 21. - 0.5 * hlp
    else:
        return 21. - hlp + 0.085 * hlp ** 2


def heat_utilisation_factor(a, heat_gains, heat_loss):
    gamma = heat_gains / heat_loss
    if 1 in gamma:
        # !!! Is this really right??
        raise Exception("Do we ever get here?")
        return numpy.where(gamma != 1,
                           (1 - gamma ** a) / (1 - gamma ** (a + 1)),
                           a / (a + 1))
    else:
        return (1 - gamma ** a) / (1 - gamma ** (a + 1))


def systems(dwelling):
    dwelling.Q_main_1 = dwelling.fraction_of_heat_from_main * dwelling.main_heating_fraction * dwelling.Q_required

    dwelling.sys1_space_effy = dwelling.main_sys_1.space_heat_effy(dwelling.Q_main_1)

    dwelling.Q_spaceheat_main = 100 * dwelling.Q_main_1 / dwelling.sys1_space_effy

    if dwelling.get('main_sys_2'):
        dwelling.Q_main_2 = dwelling.fraction_of_heat_from_main * \
                            dwelling.main_heating_2_fraction * dwelling.Q_required

        dwelling.sys2_space_effy = dwelling.main_sys_2.space_heat_effy(dwelling.Q_main_2)

        dwelling.Q_spaceheat_main_2 = 100 * dwelling.Q_main_2 / dwelling.sys2_space_effy

    else:
        dwelling.Q_spaceheat_main_2 = numpy.zeros(12)
        dwelling.Q_main_2 = [0, ]

    if dwelling.fraction_of_heat_from_main < 1:
        Q_secondary = (1 - dwelling.fraction_of_heat_from_main) * dwelling.Q_required

        dwelling.secondary_space_effy = dwelling.secondary_sys.space_heat_effy(Q_secondary)
        dwelling.Q_spaceheat_secondary = 100 * Q_secondary / dwelling.secondary_space_effy

    else:
        dwelling.Q_spaceheat_secondary = numpy.zeros(12)

    dwelling.water_effy = dwelling.water_sys.water_heat_effy(dwelling.output_from_water_heater)

    if hasattr(dwelling.water_sys, "keep_hot_elec_consumption"):
        dwelling.Q_waterheat = 100 * (
            dwelling.output_from_water_heater - dwelling.combi_loss_monthly) / dwelling.water_effy
    else:
        dwelling.Q_waterheat = 100 * dwelling.output_from_water_heater / dwelling.water_effy

    dwelling.Q_spacecooling = dwelling.Q_cooling_required / dwelling.cooling_seer


def chp(dwelling):
    if dwelling.get('chp_water_elec'):
        e_summer = dwelling.chp_water_elec
        e_space = dwelling.chp_space_elec

        # !!! Can micro chp be a second main system??

        # !!! Need water heating only option
        if dwelling.water_sys is dwelling.main_sys_1:
            if dwelling.get('use_immersion_heater_summer') and dwelling.use_immersion_heater_summer:
                b64 = sum(x[0] for x in
                          zip(dwelling.output_from_water_heater,
                              dwelling.Q_required)
                          if x[1] > 0)
            else:
                b64 = sum(dwelling.output_from_water_heater)
        else:
            b64 = 0
            e_summer = 0

        b98 = sum(dwelling.Q_required)
        b204 = dwelling.fraction_of_heat_from_main * \
               dwelling.main_heating_fraction

        # !!! Need to check sign of result

        dwelling.chp_electricity = -(b98 * b204 * e_space + b64 * e_summer)
        dwelling.chp_electricity_onsite_fraction = 0.4
    else:
        dwelling.chp_electricity = 0
        dwelling.chp_electricity_onsite_fraction = 0


def sap(dwelling):
    sap_rating_energy_cost = dwelling.fuel_cost
    ecf = 0.47 * sap_rating_energy_cost / (dwelling.GFA + 45)
    dwelling.sap_energy_cost_factor = ecf
    dwelling.sap_value = 117 - 121 * math.log10(ecf) if ecf >= 3.5 else 100 - 13.95 * ecf

    report = dwelling.report
    report.start_section("", "SAP Calculation")
    report.add_single_result("SAP value", "258", dwelling.sap_value)


def fee(dwelling):
    dwelling.fee_rating = (sum(dwelling.Q_required) + sum(dwelling.Q_cooling_required)) / dwelling.GFA

    r = dwelling.report
    r.start_section("", "FEE Calculation")
    r.add_single_result(
            "Fabric energy efficiency (kWh/m2)", "109", dwelling.fee_rating)


def der(dwelling):
    dwelling.der_rating = dwelling.emissions / dwelling.GFA

    r = dwelling.report
    r.start_section("", "DER Calculation")
    r.add_single_result(
            "Dwelling emissions (kg/yr)", "272", dwelling.emissions)
    r.add_single_result("DER rating (kg/m2/year)", "273", dwelling.der_rating)


def ter(dwelling, heating_fuel):
    # Need to convert from 2010 emissions factors used in the calc to
    # 2006 factors
    C_h = ((dwelling.emissions_water +
            dwelling.emissions_heating_main) / dwelling.main_sys_1.fuel.emission_factor_adjustment +
           (dwelling.emissions_heating_secondary +
            dwelling.emissions_fans_and_pumps) / dwelling.electricity_tariff.emission_factor_adjustment)
    C_l = dwelling.emissions_lighting / \
          dwelling.electricity_tariff.emission_factor_adjustment

    FF = heating_fuel.fuel_factor
    EFA_h = heating_fuel.emission_factor_adjustment
    EFA_l = dwelling.electricity_tariff.emission_factor_adjustment
    dwelling.ter_rating = (C_h * FF * EFA_h + C_l * EFA_l) * (
        1 - 0.2) * (1 - 0.25) / dwelling.GFA

    r = dwelling.report
    r.start_section("", "TER Calculation")
    r.add_single_result(
            "Emissions per m2 for space and water heating", "272a", C_h / dwelling.GFA)
    r.add_single_result(
            "Emissions per m2 for lighting", "272b", C_l / dwelling.GFA)
    r.add_single_result("Heating fuel factor", None, FF)
    r.add_single_result("Heating fuel emission factor adjustment", None, EFA_h)
    r.add_single_result("Electricity emission factor adjustment", None, EFA_l)
    r.add_single_result("TER", 273, dwelling.ter_rating)


def perform_demand_calc(dwelling):
    """
    Calculate the SAP energy demand for a dwelling
    :param dwelling:
    :return:
    """
    ventilation(dwelling)
    heat_loss(dwelling)
    hot_water_use(dwelling)
    internal_heat_gain(dwelling)
    solar(dwelling)
    heating_requirement(dwelling)
    cooling_requirement(dwelling)
    water_heater_output(dwelling)


def perform_full_calc(dwelling):
    """
    Perform a full SAP worksheet calculation on a dwelling, adding the results
    to the dwelling provided.
    This performs a demand calculation, and a renewable energies calculation

    :param dwelling:
    :return:
    """
    perform_demand_calc(dwelling)
    systems(dwelling)
    appendix_m.pv(dwelling)
    appendix_m.wind_turbines(dwelling)
    appendix_m.hydro(dwelling)
    chp(dwelling)
    fuel_use(dwelling)
