# Copyright (c) 2020, Djaodjin Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS;
# OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR
# OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""Forms for the signup app"""

from captcha.fields import ReCaptchaField
from django import forms
from django.contrib.auth import password_validation, get_user_model
from django.contrib.auth.forms import (AuthenticationForm,
    PasswordResetForm as PasswordResetBaseForm)
from django.utils.translation import ugettext_lazy as _

from . import settings
from .compat import six
from .helpers import full_name_natural_split
from .models import Contact


class NameEmailForm(forms.Form):
    """
    Form for frictionless registration of a new account. Just supply
    a full name and an email and you are in. We will ask for username
    and password later.
    """
    email = forms.EmailField(
        widget=forms.TextInput(attrs={'placeholder':'Email',
                                      'maxlength': 75}),
        label=_("E-mail address"))
    full_name = forms.RegexField(
        regex=settings.FULL_NAME_PAT, max_length=60,
        widget=forms.TextInput(attrs={
            'placeholder': 'Ex: first name and last name'}),
        label=_("Full name"),
        error_messages={'invalid':
            _("Sorry we do not recognize some characters in your full name.")})

    def __init__(self, *args, **kwargs):
        super(NameEmailForm, self).__init__(*args, **kwargs)
        if settings.REQUIRES_RECAPTCHA:
            self.fields['captcha'] = ReCaptchaField()


class PasswordConfirmMixin(object):

    new_password = forms.CharField(strip=False,
        label=_("New password"),
        widget=forms.PasswordInput(
            attrs={'placeholder': _("New password")}),
        help_text=password_validation.password_validators_help_text_html())
    new_password2 = forms.CharField(strip=False,
        label=_("Confirm password"),
        widget=forms.PasswordInput(
            attrs={'placeholder': _("Type password again")}))

    def clean_new_password(self):
        password = self.cleaned_data.get('new_password')
        password_validation.validate_password(password, self.user)
        return password

    def clean(self):
        """
        Validates that both passwords respectively match.
        """
        super(PasswordConfirmMixin, self).clean()
        if not ('new_password' in self._errors
            or 'new_password2' in self._errors):
            if ('new_password' in self.cleaned_data and
                'new_password2' in self.cleaned_data):
                new_password = self.cleaned_data.get('new_password', False)
                new_password2 = self.cleaned_data.get('new_password2', True)
                if new_password != new_password2:
                    self._errors['new_password'] = self.error_class([
                        _("This field does not match password confirmation.")])
                    self._errors['new_password2'] = self.error_class([
                        _("This field does not match password.")])
                    if 'new_password' in self.cleaned_data:
                        del self.cleaned_data['new_password']
                    if 'new_password2' in self.cleaned_data:
                        del self.cleaned_data['new_password2']
                    raise forms.ValidationError(
                        _("Password and password confirmation do not match."))
        return self.cleaned_data


class PasswordUpdateForm(PasswordConfirmMixin, forms.ModelForm):

    submit_title = _("Update")

    new_password = forms.CharField(strip=False,
        label=_("New password"),
        widget=forms.PasswordInput(
            attrs={'placeholder': _("New password")}),
        help_text=password_validation.password_validators_help_text_html())
    new_password2 = forms.CharField(strip=False,
        label=_("Confirm password"),
        widget=forms.PasswordInput(
            attrs={'placeholder': _("Type password again")}))

    class Meta:
        model = get_user_model()
        fields = ['new_password', 'new_password2']

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('instance')
        super(PasswordUpdateForm, self).__init__(*args, **kwargs)

    def save(self, commit=True):
        new_password = self.cleaned_data['new_password']
        self.user.set_password(new_password)
        if commit:
            self.user.save()
        return self.user


class PasswordResetConfirmForm(PasswordUpdateForm):

    pass


class PasswordChangeForm(PasswordUpdateForm):

    password = forms.CharField(strip=False,
        label=_("Your password"),
        widget=forms.PasswordInput(
            attrs={'placeholder': _("Your password")}))

    class Meta:
        model = get_user_model()
        fields = ['password', 'new_password', 'new_password2']


class PasswordResetForm(PasswordResetBaseForm):

    pass


class ActivationForm(PasswordConfirmMixin, forms.Form):
    """
    Form to set password, and optionally user's profile information
    in an activation view.
    """
    submit_title = _("Activate")

    error_messages = {
        'password_mismatch': _("Password and password confirmation"\
        " do not match."),
    }

    email = forms.EmailField(
        widget=forms.TextInput(attrs={'placeholder':'Email', 'maxlength': 75}),
        label=_("E-mail address"), disabled=True)
    full_name = forms.RegexField(
        regex=r'^[\w\s]+$', max_length=60,
        widget=forms.TextInput(attrs={'placeholder':'Full name'}),
        label=_("Full name"),
        error_messages={'invalid':
            _("Sorry we do not recognize some characters in your full name.")})
    username = forms.SlugField(widget=forms.TextInput(
        attrs={'placeholder': _("Username")}),
        max_length=30, label=_("Username"),
        error_messages={'invalid': _("Username may only contain letters,"\
            " digits and -/_ characters. Spaces are not allowed.")})
    new_password = forms.CharField(strip=False,
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'placeholder': _("Password")}),
        help_text=password_validation.password_validators_help_text_html())
    new_password2 = forms.CharField(strip=False,
        label=_("Confirm password"),
        widget=forms.PasswordInput(attrs={
            'placeholder': _("Confirm password")}))

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('instance')
        super(ActivationForm, self).__init__(*args, **kwargs)
        if settings.REQUIRES_RECAPTCHA:
            self.fields['captcha'] = ReCaptchaField()


class UserActivateForm(forms.Form):
    """
    Verfication of an e-mail address
    """
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'placeholder': _("Password")}),
        strip=False,
        help_text=password_validation.password_validators_help_text_html())

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('instance')
        super(UserActivateForm, self).__init__(*args, **kwargs)


class PublicKeyForm(forms.Form):

    submit_title = _("Update")

    pubkey = forms.CharField(widget=forms.Textarea)
    password = forms.CharField(
        label=_("Password"),
        widget=forms.PasswordInput(attrs={'placeholder': _("Password")}),
        strip=False,
        help_text=password_validation.password_validators_help_text_html())

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('instance')
        super(PublicKeyForm, self).__init__(*args, **kwargs)


class UserForm(forms.ModelForm):
    """
    Form to update a ``User`` profile.
    """
    submit_title = _("Update")

    username = forms.SlugField(widget=forms.TextInput(
        attrs={'placeholder': _("Username")}),
        max_length=30, label=_("Username"),
        error_messages={'invalid': _("Username may only contain letters,"\
            " digits and -/_ characters. Spaces are not allowed.")})
    full_name = forms.CharField(widget=forms.TextInput(
        attrs={'placeholder': _("First and last names")}),
        max_length=254, label=_("Full name"))

    class Meta:
        model = get_user_model()
        fields = ['username', 'email', 'full_name']


    def clean_full_name(self):
        if self.cleaned_data.get('full_name'):
            first_name, mid_name, last_name = \
                full_name_natural_split(
                    self.cleaned_data.get('full_name'), middle_initials=False)
            if mid_name:
                first_name = (first_name + " " + mid_name).strip()
            self.cleaned_data.update({
                'first_name': first_name,
                'last_name': last_name})
            if self.instance:
                self.instance.first_name = first_name
                self.instance.last_name = last_name
        return self.cleaned_data.get('full_name')


class UserNotificationsForm(forms.Form):
    """
    Form to update a ``User`` notification preferences.
    """
    submit_title = _("Update")

    def __init__(self, instance, *args, **kwargs):
        #pylint:disable=unused-argument
        super(UserNotificationsForm, self).__init__(*args, **kwargs)
        for item, initial in six.iteritems(self.initial.get('notifications')):
            self.fields[item] = forms.BooleanField(
                label=initial[0].title, help_text=initial[0].description,
                required=False, initial=initial[1])


class StartAuthenticationForm(forms.Form):
    """
    Form to present a user who may or may not have an account yet.
    """
    username = forms.EmailField(widget=forms.TextInput(
        attrs={'placeholder':'Email', 'maxlength': 254}),
        label=_("Please enter your e-mail address"))

    submit_title = _("Submit")

    def __init__(self, request=None, *args, **kwargs):
        super(StartAuthenticationForm, self).__init__(*args, **kwargs)


class UsernameOrEmailAuthenticationForm(AuthenticationForm):

    # The field is called `username`, yet it is technically
    # a username or e-mail.
    username = forms.CharField(widget=forms.TextInput(
        attrs={'placeholder': _("Username or e-mail")}),
        max_length=254, label=_("Username or e-mail"))
    password = forms.CharField(widget=forms.PasswordInput(
        attrs={'placeholder': _("Password")}), label=_("Password"))

    def __init__(self, *args, **kwargs):
        super(UsernameOrEmailAuthenticationForm, self).__init__(*args, **kwargs)
        username_label = self.initial.get('username_label', None)
        if username_label:
            placeholder_label = _('%(username)s or e-mail' % {
                'username': username_label})
            self.fields['username'].label = placeholder_label
            self.fields['username'].widget.attrs['placeholder'] \
                = placeholder_label


class MFACodeForm(UsernameOrEmailAuthenticationForm):

    code = forms.IntegerField(widget=forms.TextInput(
        attrs={'placeholder': _("One-time authentication code"),
            'autofocus': True}),
        label=_("One-time authentication code"))

    def __init__(self, *args, **kwargs):
        super(MFACodeForm, self).__init__(*args, **kwargs)
        self.fields['username'].widget = forms.HiddenInput()
        self.fields['password'].widget = forms.HiddenInput()

    def clean(self):
        super(MFACodeForm, self).clean()
        code = self.cleaned_data.get('code')
        contact = Contact.objects.filter(user=self.user_cache).first()
        if not contact or code != contact.mfa_priv_key:
            raise forms.ValidationError(_("MFA code does not match."))
        return self.cleaned_data
