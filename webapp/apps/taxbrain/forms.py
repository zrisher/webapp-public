from __future__ import print_function
from django import forms
from django.forms import ModelForm
from django.utils.translation import ugettext_lazy as _

from .models import TaxSaveInputs
from .helpers import (TaxCalcField, TaxCalcParam, default_policy, is_number,
                      int_to_nth, is_string, string_to_float_array)
                      

def bool_like(x):
    b = True if x == 'True' or x == True else False
    return b


TAXCALC_DEFAULTS_2016 = default_policy(2016)


class PersonalExemptionForm(ModelForm):

    def __init__(self, first_year, *args, **kwargs):
        self._first_year = int(first_year)
        self._default_params = default_policy(self._first_year)
        # Defaults are set in the Meta, but we need to swap
        # those outs here in the init because the user may
        # have chosen a different start year
        all_defaults = []
        for param in self._default_params.values():
            for field in param.col_fields:
                all_defaults.append((field.id, field.default_value))

        for _id, default in all_defaults:
            self._meta.widgets[_id].attrs['placeholder'] = default

        # If a stored instance is passed,
        # set CPI flags based on the values in this instance
        if 'instance' in kwargs:
            instance = kwargs['instance']
            cpi_flags = [attr for attr in dir(instance) if attr.endswith('_cpi')]
            for flag in cpi_flags:
                if getattr(instance, flag) is not None and flag in self._meta.widgets:
                    self._meta.widgets[flag].attrs['placeholder'] = getattr(instance, flag)

        super(PersonalExemptionForm, self).__init__(*args, **kwargs)

    def get_comp_data(self, comp_key, param_id, col, required_length):
        """
        Get the data necessary for a min/max validation comparison, given:
            param_id - The webapp-internal TC parameter ID
            col - The column number for the param
            comp_key - a key that is either:
               * a static value
               * the word 'default' - we should use the field's defaults
               * the name of another param - we should use that param's
                 corresponding column field. If values have been submitted for
                 it, use them. Otherwise use its defaults
             required_len - The number of values we need to compare against

        After finding the proper data, expand it to the required_length by
        repeating the final value.

        @todo: CPI inflate the final value to fill instead of simply repeating
        """

        base_param = self._default_params[param_id]
        base_col = base_param.col_fields[col]

        if is_number(comp_key):
            comp_data = [comp_key]
            source = "the static value"
        elif comp_key == 'default':
            comp_data = base_col.values
            source = "this field's default"
        elif comp_key in self._default_params:
            other_param = self._default_params[comp_key]
            other_col = other_param.col_fields[col]
            other_values = None

            if other_col.id in self.cleaned_data:
                other_values_raw = self.cleaned_data[other_col.id]
                other_values = string_to_float_array(other_values_raw)

            if other_values:
                comp_data = other_values
                source = other_param.name + "'s value"
            else:
                other_defaults = other_col.values
                comp_data = other_defaults
                source = other_param.name + "'s default"
        else:
            raise ValueError('Unknown comp keyword "{0}"'.format(comp_key))

        if len(comp_data) < 1:
            raise ValueError('No comparison data found for kw'.format(comp_key))

        len_diff = required_length - len(comp_data)
        if len_diff > 0:
            comp_data += [comp_data[-1]] * len_diff

        return {'source': source, 'comp_data': comp_data}

    def clean(self):
        """
        " This method should be used to provide custom model validation, and to
        modify attributes on your model if desired. For instance, you could use
        it to automatically provide a value for a field, or to do validation
        that requires access to more than a single field."
        per https://docs.djangoproject.com/en/1.8/ref/models/instances/

        Note that this can be defined both on forms and on the model, but is
        only automatically called on form submissions.
        """
        self.do_taxcalc_validations()
        self.add_errors_on_extra_inputs()

    def add_errors_on_extra_inputs(self):
        ALLOWED_EXTRAS = {'has_errors', 'start_year', 'csrfmiddlewaretoken'}
        all_inputs = set(self.data.keys())
        allowed_inputs= set(self.fields.keys())
        extra_inputs = all_inputs - allowed_inputs - ALLOWED_EXTRAS
        for _input in extra_inputs:
            self.add_error(None, "Extra input '{0}' not allowed".format(_input))

    def do_taxcalc_validations(self):
        """
        Run the validations specified by Taxcalc's param definitions

        Each parameter can be assigned a min and a max, the value of which can
        be statically defined or determined dynamically via a keyword.

        Keywords correlate to submitted value array for a different parameter,
        or to the default value array for the validated field.

        We could define these on individual fields instead, but we would need to
        define all the field data dynamically both here and on the model,
        and it's not yet possible on the model due to issues with how migrations
        are detected.
        """

        for param_id, param in self._default_params.items():
            if param.coming_soon or param.hidden:
                continue

            if param.max is None and param.min is None:
                continue

            for col, col_field in enumerate(param.col_fields):
                submitted_col_values_raw = self.cleaned_data[col_field.id]
                submitted_col_values = string_to_float_array(submitted_col_values_raw)
                default_col_values = col_field.values

                # If we change a different field which this field relies on for
                # validation, we must ensure this is validated even if unchanged
                # from defaults
                if submitted_col_values:
                    col_values = submitted_col_values
                else:
                    col_values = default_col_values

                if param.max is not None:
                    comp = self.get_comp_data(param.max, param_id, col, len(col_values))
                    source = comp['source']
                    maxes = comp['comp_data']

                    for i, value in enumerate(col_values):
                        if value > maxes[i]:
                            if len(col_values) == 1:
                                self.add_error(col_field.id,
                                               u"Must be \u2264 {0} of {1}".
                                               format(source, maxes[i]))
                            else:
                                self.add_error(col_field.id,
                                               u"{0} value must be \u2264 \
                                               {1}'s {0} value of {2}".format(
                                                   int_to_nth(i + 1),
                                                   source, maxes[i]))

                if param.min is not None:
                    comp = self.get_comp_data(param.min, param_id, col, len(col_values))
                    source = comp['source']
                    mins = comp['comp_data']

                    for i, value in enumerate(col_values):
                        if value < mins[i]:
                            if len(col_values) == 1:
                                self.add_error(col_field.id,
                                               u"Must be \u2265 {0} of {1}".
                                               format(source, mins[i]))
                            else:
                                self.add_error(col_field.id,
                                               u"{0} value must be \u2265 \
                                               {1}'s {0} value of {2}".format(
                                                   int_to_nth(i + 1),
                                                   source, mins[i]))

    class Meta:
        model = TaxSaveInputs
        exclude = ['creation_date']
        widgets = {}
        labels = {}

        for param in TAXCALC_DEFAULTS_2016.values():
            for field in param.col_fields:
                attrs = {
                    'class': 'form-control',
                    'placeholder': field.default_value,
                }

                if param.coming_soon:
                    attrs['disabled'] = True

                if param.tc_id == '_ID_BenefitSurtax_Switch':
                    """switches = ['ID_BenefitSurtax_Switch_' + str(i) for i in range(6)]
                    print("getting the model attribute")
                    for switch in switches:
                        print("switch is ", switch)
                        val = getattr(model, switch)
                        print("val is ", val)"""
                    attrs['checked'] = False
                    widgets[field.id] = forms.CheckboxInput(attrs=attrs, check_test=bool_like)
                else:
                    widgets[field.id] = forms.TextInput(attrs=attrs)

                labels[field.id] = field.label

            if param.inflatable:
                field = param.cpi_field
                attrs = {
                    'class': 'form-control sr-only',
                    'placeholder': bool(field.default_value),
                }

                if param.coming_soon:
                    attrs['disabled'] = True

                widgets[field.id] = forms.NullBooleanSelect(attrs=attrs)


        # Keeping label text, may want to use some of these custom labels
        # instead of those specified in params.json
        """
        labels = {
            'FICA_trt': _('Combined FICA rate'),
            'SS_Income_c': _('Max Taxable Earnings'),
            'SS_percentage1': _('Rate 1'),
            'SS_thd50_0': _('Single'),
            'SS_thd50_1': _('Married filing Jointly'),
            'SS_thd50_2': _('Head of Household'),
            'SS_thd50_3': _('Married filing Separately'),
            'SS_percentage2': _('Rate 2'),
            'SS_thd85_0': _('Single'),
            'SS_thd85_1': _('Married filing Jointly'),
            'SS_thd85_2': _('Head of Household'),
            'SS_thd85_3': _('Married filing Separately'),

            'AMED_trt': _('Threshold'),
            'AMED_thd_0': _('Single'),
            'AMED_thd_1': _('Married filing Jointly'),
            'AMED_thd_2': _('Head of Household'),
            'AMED_thd_3': _('Married filing Separately'),

            'ALD_StudentLoan_HC': _('Student Loan Interest Deduction'),
            'ALD_SelfEmploymentTax_HC': _('Deduction Half of Self-Employment Tax'),
            'ALD_SelfEmp_HealthIns_HC': _('Self Employed Health Insurance Deduction'),
            'ALD_KEOGH_SEP_HC': _('Payment to KEOGH Plan and SEP Deduction'),
            'ALD_EarlyWithdraw_HC': _('Forfeited Int. Penalty - Early Withdrawal of Savings'),
            'ALD_Alimony_HC': _('Alimony Paid'),
            'FEI_ec_c': _('Foreign earned income exclusion'),

            'II_em': _('Amount'),
            'II_prt': _('Phaseout'),
            'II_em_ps_0': _('Single'),
            'II_em_ps_1': _('Married filing Jointly'),
            'II_em_ps_2': _('Head of Household'),
            'II_em_ps_3': _('Married filing Separately'),

            'STD_0': _('Single'),
            'STD_1': _('Married filing Jointly'),
            'STD_2': _('Head of Household'),
            'STD_3': _('Married filing Separately'),
            'STD_Aged_0': _('Single'),
            'STD_Aged_1': _('Married filing Jointly'),
            'STD_Aged_2': _('Head of Household'),
            'STD_Aged_3': _('Married filing Separately'),

            'ID_medical_frt': _('Floor as a % of Income'),
            'ID_Casualty_frt': _('Floor as a % of Income'),
            'ID_Miscellaneous_frt': _('Floor as a % of Income'),
            'ID_Charity_crt_Cash': _('Ceiling for cash as % of AGI'),
            'ID_Charity_crt_Asset': _('Ceiling for assets as % of AGI'),
            'ID_ps_0': _('Single'),
            'ID_ps_1': _('Married filing Jointly'),
            'ID_ps_2': _('Head of Household'),
            'ID_ps_3': _('Married filing Separately'),
            'ID_prt': _('Phaseout Rate'),
            'ID_crt': _('Max Percent Forfeited'),
            'ID_BenefitSurtax_trt': _('Surtax rate'),
            'ID_BenefitSurtax_crt': _('Credit on surtax (percent of AGI)'),
            'ID_BenefitSurtax_Switch_0': _('Medical deduction'),
            'ID_BenefitSurtax_Switch_1': _('State and local deduction (incl real estate taxes'),
            'ID_BenefitSurtax_Switch_2': _('Casualty deduction'),
            'ID_BenefitSurtax_Switch_3': _('Miscellaneous deduction'),
            'ID_BenefitSurtax_Switch_4': _('Intrest paid deduction (incl business and mortgage)'),
            'ID_BenefitSurtax_Switch_5': _('Charitable deduction'),

            'CG_rt1': _('Rate 1'),
            'CG_thd1_0': _('Single'),
            'CG_thd1_1': _('Married filing Jointly'),
            'CG_thd1_2': _('Head of Household'),
            'CG_thd1_3': _('Married filing Separately'),
            'CG_rt2': _('Rate 2'),
            'CG_thd2_0': _('Single'),
            'CG_thd2_1': _('Married filing Jointly'),
            'CG_thd2_2': _('Head of Household'),
            'CG_thd2_3': _('Married filing Separately'),
            'CG_rt3': _('Rate 3'),
            'Dividend_rt1': _('Rate 1'),
            'Dividend_thd1_0': _('Single'),
            'Dividend_thd1_1': _('Married filing Jointly'),
            'Dividend_thd1_2': _('Head of Household'),
            'Dividend_thd1_3': _('Married filing Separately'),
            'Dividend_rt2': _('Rate 2'),
            'Dividend_thd2_0': _('Single'),
            'Dividend_thd2_1': _('Married filing Jointly'),
            'Dividend_thd2_2': _('Head of Household'),
            'Dividend_thd2_3': _('Married filing Separately'),
            'Dividend_rt3': _('Rate 3'),
            'Dividend_thd3_0': _('Single'),
            'Dividend_thd3_1': _('Married filing Jointly'),
            'Dividend_thd3_2': _('Head of Household'),
            'Dividend_thd3_3': _('Married filing Separately'),

            'NIIT_trt': _('Rate'),
            'NIIT_thd_0': _('Single'),
            'NIIT_thd_1': _('Married filing Jointly'),
            'NIIT_thd_2': _('Head of Household'),
            'NIIT_thd_3': _('Married filing Separately'),
            'II_rt1': _('Rate 1'),
            'II_brk1_0': _('Single'),
            'II_brk1_1': _('Married filing Jointly'),
            'II_brk1_2': _('Head of Household'),
            'II_brk1_3': _('Married filing Separately'),
            'II_rt2': _('Rate 2'),
            'II_brk2_0': _('Single'),
            'II_brk2_1': _('Married filing Jointly'),
            'II_brk2_2': _('Head of Household'),
            'II_brk2_3': _('Married filing Separately'),
            'II_rt3': _('Rate 3'),
            'II_brk3_0': _('Single'),
            'II_brk3_1': _('Married filing Jointly'),
            'II_brk3_2': _('Head of Household'),
            'II_brk3_3': _('Married filing Separately'),
            'II_rt4': _('Rate 4'),
            'II_brk4_0': _('Single'),
            'II_brk4_1': _('Married filing Jointly'),
            'II_brk4_2': _('Head of Household'),
            'II_brk4_3': _('Married filing Separately'),
            'II_rt5': _('Rate 5'),
            'II_brk5_0': _('Single'),
            'II_brk5_1': _('Married filing Jointly'),
            'II_brk5_2': _('Head of Household'),
            'II_brk5_3': _('Married filing Separately'),
            'II_rt6': _('Rate 6'),
            'II_brk6_0': _('Single'),
            'II_brk6_1': _('Married filing Jointly'),
            'II_brk6_2': _('Head of Household'),
            'II_brk6_3': _('Married filing Separately'),
            'II_rt7': _('Rate 7'),

            'AMT_em_0': _('Single'),
            'AMT_em_1': _('Married filing Jointly'),
            'AMT_em_2': _('Head of Household'),
            'AMT_em_3': _('Married filing Separately'),
            'AMT_prt': _('Phaseout Rate'),
            'AMT_em_ps_0': _('Single'),
            'AMT_em_ps_1': _('Married filing Jointly'),
            'AMT_em_ps_2': _('Head of Household'),
            'AMT_em_ps_3': _('Married filing Separately'),
            'AMT_trt1': _('AMT rate'),
            'AMT_trt2': _('Surtax rate'),
            'AMT_tthd': _('Surtax Threshold'),

            'EITC_rt_0': _('0 Kids'),
            'EITC_rt_1': _('1 Kid'),
            'EITC_rt_2': _('2 Kids'),
            'EITC_rt_3': _('3+ Kids'),
            'EITC_prt_0': _('0 Kids'),
            'EITC_prt_1': _('1 Kid'),
            'EITC_prt_2': _('2 Kids'),
            'EITC_prt_3': _('3+ Kids'),
            'EITC_ps_0': _('0 Kids'),
            'EITC_ps_1': _('1 Kid'),
            'EITC_ps_2': _('2 Kids'),
            'EITC_ps_3': _('3+ Kids'),
            'EITC_c_0': _('0 Kids'),
            'EITC_c_1': _('1 Kid'),
            'EITC_c_2': _('2 Kids'),
            'EITC_c_3': _('3+ Kids'),
            'CTC_c': _('Max Credit'),
            'CTC_prt': _('Phaseout Rate'),
            'CTC_ps_0': _('Single'),
            'CTC_ps_1': _('Married filing Jointly'),
            'CTC_ps_2': _('Head of Household'),
            'CTC_ps_3': _('Married filing Separately'),
            'ACTC_rt': _('Rate'),
            'ACTC_ChildNum': _('Qualifying # of children'),
        }
        """

def has_field_errors(form):
    """
    This allows us to see if we have field_errors, as opposed to only having
    form.non_field_errors. I would prefer to put this in a template tag, but
    getting that working with a conditional statement in a template was very
    challenging.
    """
    if not form.errors:
        return False

    for field in form:
        if field.errors:
            return True

    return False
