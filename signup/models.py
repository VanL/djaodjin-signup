# Copyright (c) 2022, Djaodjin Inc.
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

"""
User Model for the signup app
"""
from __future__ import absolute_import
from __future__ import unicode_literals

import datetime, hashlib, logging, random, re, string

from django.core.exceptions import ValidationError
from django.core.validators import (RegexValidator, URLValidator,
    validate_email as validate_email_base)
from django.contrib.auth import get_user_model
from django.contrib.auth.models import UserManager
from django.db import models, transaction, IntegrityError
from django.template.defaultfilters import slugify
from django.utils.translation import ugettext_lazy as _
from django.contrib.auth.hashers import check_password, make_password
from phonenumber_field.modelfields import PhoneNumberField
from rest_framework.exceptions import ValidationError as DRFValidationError

from . import settings, signals
from .backends.mfa import EmailMFABackend
from .compat import import_string, python_2_unicode_compatible, six
from .helpers import datetime_or_now, full_name_natural_split
from .utils import generate_random_slug, handle_uniq_error, has_invalid_password
from .validators import validate_phone


LOGGER = logging.getLogger(__name__)
EMAIL_VERIFICATION_RE = re.compile('^%s$' % settings.EMAIL_VERIFICATION_PAT)


def _get_extra_field_class():
    extra_class = settings.EXTRA_FIELD
    if extra_class is None:
        extra_class = models.TextField
    elif isinstance(extra_class, str):
        extra_class = import_string(extra_class)
    return extra_class


def domain_name_validator(value):
    """
    Validates that the given value contains no whitespaces to prevent common
    typos.
    """
    if not value:
        return
    checks = ((s in value) for s in string.whitespace)
    if any(checks):
        raise ValidationError(
            _("The domain name cannot contain any spaces or tabs."),
            code='invalid',
        )


class ActivatedUserManager(UserManager):

    @classmethod
    def normalize_email(cls, email):
        if email is None:
            # Overrides `email or ''` in superclass such that we can
            # create User models with no e-mail address.
            return email
        return super(ActivatedUserManager, cls).normalize_email(email)

    def create_user_from_email(self, email, password=None, **kwargs):
        #pylint:disable=protected-access
        field = self.model._meta.get_field('username')
        max_length = field.max_length
        username = slugify(email.split('@')[0])
        try:
            field.run_validators(username)
        except ValidationError:
            prefix = 'user_'
            username = generate_random_slug(
                length=min(max_length - len(prefix), 40), prefix=prefix)
        err = IntegrityError()
        trials = 0
        username_base = username
        while trials < 10:
            try:
                with transaction.atomic():
                    return super(ActivatedUserManager, self).create_user(
                        username, email=email, password=password, **kwargs)
            except IntegrityError as exp:
                err = exp
                if len(username_base) + 4 > max_length:
                    username_base = username_base[:(max_length - 4)]
                username = generate_random_slug(
                    length=len(username_base) + 4, prefix=username_base + '-',
                    allowed_chars='0123456789')
                trials = trials + 1
        raise err

    def create_user_from_phone(self, phone, password=None, **kwargs):
        #pylint:disable=protected-access
        field = self.model._meta.get_field('username')
        max_length = field.max_length
        prefix = 'user_'
        username = generate_random_slug(
            length=min(max_length - len(prefix), 40), prefix=prefix)
        try:
            field.run_validators(username)
        except ValidationError:
            username = 'user'
        err = IntegrityError()
        trials = 0
        username_base = username
        while trials < 10:
            try:
                with transaction.atomic():
                    return super(ActivatedUserManager, self).create_user(
                        username, password=password, **kwargs)
            except IntegrityError as exp:
                err = exp
                if len(username_base) + 4 > max_length:
                    username_base = username_base[:(max_length - 4)]
                username = generate_random_slug(
                    length=len(username_base) + 4, prefix=username_base + '-',
                    allowed_chars='0123456789')
                trials = trials + 1
        raise err

    def create_user(self, username, email=None, password=None, **kwargs):
        """
        Create an inactive user with a default username.

        We have a different notion of an active user than Django does.
        For Django when is_active is False, the user cannot be identified
        and requests fall back to Anonymous. That's a problem because
        we want a user who has given us a name and email address to be
        able to use the site. We only require a password for the second
        login. Our definition of inactive is thus a user that has an invalid
        password.
        """
        phone = kwargs.pop('phone', None)
        lang = kwargs.pop('lang', None)
        extra = kwargs.pop('extra', None)
        uses_sso_provider = False
        if email:
            domain = email.split('@')[-1]
            uses_sso_provider = bool(DelegateAuth.objects.filter(
                domain=domain).exists())
        if uses_sso_provider:
            # Prevents `check_has_credentials`, hence `fail_active`
            # to trigger a re-login when using a SSO provider.
            # By definition, all users created through a SSO provider
            # are active.
            password = "*****"
        with transaction.atomic():
            if username:
                user = super(ActivatedUserManager, self).create_user(
                    username, email=email, password=password, **kwargs)
            elif email:
                user = self.create_user_from_email(
                    email, password=password, **kwargs)
            elif phone:
                user = self.create_user_from_phone(
                    phone, password=password, **kwargs)
            else:
                raise ValueError("email or phone must be set.")
            if email:
                # Force is_active to True and create an email verification key
                # (see above definition of active user).
                # In case we delegate auth to a SSO provider, we assume
                # the e-mail address has already been verified.
                if uses_sso_provider:
                    at_time = datetime_or_now()
                    try:
                        contact = Contact.objects.get(user=user, email=email)
                        contact.email_verified_at = at_time
                        contact.save()
                    except Contact.DoesNotExist:
                        create_kwargs = {
                            'user': user,
                            'email': email,
                            'full_name': user.get_full_name(),
                            'nick_name': user.first_name,
                            'email_verified_at': at_time
                        }
                        Contact.objects.create(**create_kwargs)
                else:
                    Contact.objects.prepare_email_verification(user, email,
                        phone=phone, lang=lang, extra=extra)
            elif phone:
                # Force is_active to True and create an email verification key
                # (see above definition of active user).
                Contact.objects.prepare_phone_verification(user, phone,
                    email=email, lang=lang, extra=extra)
            user.is_active = True
            user.save()
            LOGGER.info("'%s <%s>' registered with username '%s'%s%s",
                user.get_full_name(), user.email, user,
                (" and phone %s" % str(phone)) if phone else "",
                (" and preferred language %s" % str(lang)) if lang else "",
                extra={'event': 'register', 'user': user})
            signals.user_registered.send(sender=__name__, user=user)
        return user

    def find_user(self, username):
        user_kwargs = {}
        contact_kwargs = {}
        username = str(username) # We could have a ``PhoneNumber`` here.
        try:
            validate_email_base(username)
            contact_kwargs = {'email__iexact': username}
            user_kwargs = {'email__iexact': username}
        except ValidationError:
            pass
        if not contact_kwargs:
            try:
                contact_kwargs = {'phone__iexact': validate_phone(username)}
            except ValidationError:
                contact_kwargs = {'user__username__iexact': username}
                user_kwargs = {'username__iexact': username}

        if user_kwargs:
            try:
                return self.filter(is_active=True).get(**user_kwargs)
            except self.model.DoesNotExist:
                pass
        try:
            contact = Contact.objects.filter(user__is_active=True,
                **contact_kwargs).select_related('user').get()
            return contact.user
        except Contact.DoesNotExist:
            pass
        raise self.model.DoesNotExist()


class ContactManager(models.Manager):

    def find_by_username_or_comm(self, username):
        contact_kwargs = {}
        username = str(username) # We could have a ``PhoneNumber`` here.
        try:
            validate_email_base(username)
            contact_kwargs = {'email__iexact': username}
        except ValidationError:
            pass
        if not contact_kwargs:
            try:
                contact_kwargs = {'phone__iexact': validate_phone(username)}
            except ValidationError:
                contact_kwargs = {'user__username__iexact': username}

        return self.filter(user__is_active=True,
            **contact_kwargs).select_related('user')

    def get_token(self, verification_key):
        """
        Returns a ``Contact`` where a non-expired ``email_verification_key`` or
        ``phone_verification_key`` matches ``verification_key``.
        """
        if EMAIL_VERIFICATION_RE.search(verification_key):
            try:
                at_time = datetime_or_now()
                email_filter = models.Q(email_verification_key=verification_key)
                if not settings.BYPASS_VERIFICATION_KEY_EXPIRED_CHECK:
                    email_filter &= models.Q(email_verification_at__gt=at_time
                        - datetime.timedelta(days=settings.KEY_EXPIRATION))
                phone_filter = models.Q(phone_verification_key=verification_key)
                if not settings.BYPASS_VERIFICATION_KEY_EXPIRED_CHECK:
                    phone_filter &= models.Q(phone_verification_at__gt=at_time
                        - datetime.timedelta(days=settings.KEY_EXPIRATION))
                return self.filter(email_filter | phone_filter).select_related(
                    'user').get()
            except Contact.DoesNotExist:
                pass # We return None instead here.
        return None

    def prepare_email_verification(self, user, email, at_time=None,
                                   verification_key=None, reason=None,
                                   **kwargs):
        #pylint:disable=too-many-arguments
        at_time = datetime_or_now(at_time)
        if verification_key is None:
            random_key = str(random.random()).encode('utf-8')
            salt = hashlib.sha1(random_key).hexdigest()[:5]
            verification_key = hashlib.sha1(
                (salt+user.username).encode('utf-8')).hexdigest()
        # XXX The get() needs to be targeted at the write database in order
        # to avoid potential transaction consistency problems.
        try:
            with transaction.atomic():
                # We have to wrap in a transaction.atomic here, otherwise
                # we end-up with a TransactionManager error when Contact.slug
                # already exists in db and we generate new one.
                contact = self.get(user=user, email=email)
                if not contact.email_verification_key:
                    # If we already have a verification key, let's keep it.
                    # We want to avoid users clicking multiple times on a link
                    # that will trigger an activate url, then filling the first
                    # form that showed up.
                    contact.email_verification_key = verification_key
                contact.email_verification_at = at_time
                # XXX It is possible a 'reason' field would be a better
                # implementation.
                if reason:
                    contact.extra = reason
                contact.save()
                return contact, False
        except self.model.DoesNotExist:
            kwargs.update({
                'user': user,
                'email': email,
                'full_name': user.get_full_name(),
                'nick_name': user.first_name,
                'email_verification_key': verification_key,
                'email_verification_at': at_time
            })
            if reason:
                # XXX It is possible a 'reason' field would be a better
                # implementation.
                kwargs.update({'extra': reason})
        return self.create(**kwargs), True

    def prepare_phone_verification(self, user, phone, at_time=None,
                                   verification_key=None, reason=None,
                                   **kwargs):
        #pylint:disable=too-many-arguments
        at_time = datetime_or_now(at_time)
        if verification_key is None:
            random_key = str(random.random()).encode('utf-8')
            salt = hashlib.sha1(random_key).hexdigest()[:5]
            verification_key = hashlib.sha1(
                (salt+user.username).encode('utf-8')).hexdigest()
        # XXX The get() needs to be targeted at the write database in order
        # to avoid potential transaction consistency problems.
        try:
            with transaction.atomic():
                # We have to wrap in a transaction.atomic here, otherwise
                # we end-up with a TransactionManager error when Contact.slug
                # already exists in db and we generate new one.
                contact = self.get(user=user, phone=phone)
                if not contact.phone_verification_key:
                    # If we already have a verification key, let's keep it.
                    # We want to avoid users clicking multiple times on a link
                    # that will trigger an activate url, then filling the first
                    # form that showed up.
                    contact.phone_verification_key = verification_key
                contact.phone_verification_at = at_time
                # XXX It is possible a 'reason' field would be a better
                # implementation.
                if reason:
                    contact.extra = reason
                contact.save()
                return contact, False
        except self.model.DoesNotExist:
            kwargs.update({
                'user': user,
                'phone': phone,
                'full_name': user.get_full_name(),
                'nick_name': user.first_name,
                'phone_verification_key': verification_key,
                'phone_verification_at': at_time
            })
            if reason:
                # XXX It is possible a 'reason' field would be a better
                # implementation.
                kwargs.update({'extra': reason})
        return self.create(**kwargs), True

    def activate_user(self, verification_key,
                      username=None, password=None, full_name=None):
        """
        Activate a user whose email address has been verified.
        """
        #pylint:disable=too-many-arguments
        at_time = datetime_or_now()
        try:
            token = self.get_token(verification_key=verification_key)
            if token:
                LOGGER.info('user %s activated through code: %s',
                    token.user, verification_key,
                    extra={'event': 'activate', 'username': token.user.username,
                        'verification_key': verification_key,
                        'email_verification_key': token.email_verification_key,
                        'phone_verification_key': token.phone_verification_key})
                previously_inactive = has_invalid_password(token.user)
                with transaction.atomic():
                    if token.email_verification_key == verification_key:
                        token.email_verification_key = None
                        token.email_verified_at = at_time
                    elif token.phone_verification_key == verification_key:
                        token.phone_verification_key = None
                        token.phone_verified_at = at_time
                    token.save()
                    needs_save = False
                    if full_name:
                        token.full_name = full_name
                        #pylint:disable=unused-variable
                        first_name, mid_name, last_name = \
                            full_name_natural_split(full_name)
                        token.user.first_name = first_name
                        token.user.last_name = last_name
                        needs_save = True
                    if username:
                        token.user.username = username
                        needs_save = True
                    if password:
                        token.user.set_password(password)
                        needs_save = True
                    if not token.user.is_active:
                        token.user.is_active = True
                        needs_save = True
                    if needs_save:
                        token.user.save()
                return token.user, previously_inactive
        except Contact.DoesNotExist:
            pass # We return None instead here.
        return None, None

    def is_reachable_by_email(self, user):
        """
        Returns True if the user is reachable by email.
        """
        return self.filter(user=user).exclude(
            email_verified_at__isnull=True).exists()

    def is_reachable_by_phone(self, user):
        """
        Returns True if the user is reachable by phone.
        """
        return self.filter(user=user).exclude(
            phone_verified_at__isnull=True).exists()


@python_2_unicode_compatible
class Contact(models.Model):
    """
    Used in workflow to verify the email address of a ``User``.
    """
    NO_MFA = 0
    EMAIL_BACKEND = 1

    MFA_BACKEND_TYPE = (
        (NO_MFA, "password only"),
        (EMAIL_BACKEND, "send one-time authentication code through email"),
    )

    objects = ContactManager()

    slug = models.SlugField(unique=True,
        help_text=_("Unique identifier shown in the URL bar, effectively"\
            " the username for profiles with login credentials."))
    created_at = models.DateTimeField(auto_now_add=True,
        help_text=_("Date/time of creation (in ISO format)"))
    email = models.EmailField(_("E-mail address"), unique=True, null=True,
        help_text=_("E-mail address to contact user"))
    phone = PhoneNumberField(_("Phone number"), unique=True, null=True,
        help_text=_("Phone number to contact user"))
    full_name = models.CharField(_("Full name"), max_length=60, blank=True,
        help_text=_("Full name (effectively first name followed by last name)"))
    nick_name = models.CharField(_("Nick name"), max_length=60, blank=True,
        help_text=_("Short casual name used to address the user"))
    # 2083 number is used because it is a safe option to choose based
    # on some older browsers behavior
    # https://www.google.com/url?sa=t&rct=j&q=&esrc=s&source=web&cd=4&cad=rja&uact=8&ved=2ahUKEwi2hbjPwIPgAhULXCsKHQ-lAj4QFjADegQIBhAL&url=https%3A%2F%2Fstackoverflow.com%2Fquestions%2F417142%2Fwhat-is-the-maximum-length-of-a-url-in-different-browsers&usg=AOvVaw0QgMo_L7jjK0YsXchrJgOQ
    picture = models.URLField(_("URL to a profile picture"), max_length=2083,
        null=True, blank=True,
        help_text=_("URL location of the profile picture"))
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
        null=True, on_delete=models.CASCADE, related_name='contacts')
    # key must be unique when used in URLs. IF we use a code,
    # then it shouldn't be.
    email_verification_key = models.CharField(_(
        "Email verification key"),
         null=True, max_length=40)
    email_verification_at = models.DateTimeField(null=True,
        help_text=_("Date/time when the e-mail verification key was sent"))
    email_verified_at = models.DateTimeField(null=True,
        help_text=_("Date/time when the e-mail was last verified"))
    phone_verification_key = models.CharField(
        _("Phone verification key"),
        null=True, max_length=40)
    phone_verification_at = models.DateTimeField(null=True,
        help_text=_("Date/time when the phone verification key was sent"))
    phone_verified_at = models.DateTimeField(null=True,
        help_text=_("Date/time when the phone number was last verified"))
    lang = models.CharField(_("Preferred communication language"),
         default=settings.LANGUAGE_CODE, max_length=5,
        help_text=_("Two-letter ISO 639 code for the preferred"\
        " communication language (ex: en)"))
    mfa_backend = models.PositiveSmallIntegerField(
        choices=MFA_BACKEND_TYPE, default=NO_MFA,
        help_text=_("Backend to use for multi-factor authentication"))
    mfa_priv_key = models.IntegerField(
        _("One-time authentication code"), null=True)
    mfa_nb_attempts = models.IntegerField(
        _("Number of attempts to pass the MFA code"), default=0)
    extra = _get_extra_field_class()(null=True,
        help_text=_("Extra meta data (can be stringify JSON)"))

    def __str__(self):
        return str(self.slug)

    @property
    def username(self):
        return self.slug

    @property
    def printable_name(self):
        if self.nick_name:
            return self.nick_name
        if self.full_name:
            # pylint:disable=unused-variable
            first_name, mid_name, last_name = full_name_natural_split(
                self.full_name)
            return first_name
        return self.username

    def get_mfa_backend(self):
        if self.mfa_backend == self.EMAIL_BACKEND:
            return EmailMFABackend()
        return None

    def create_mfa_token(self):
        return self.get_mfa_backend().create_token(self)

    def clear_mfa_token(self):
        self.mfa_priv_key = None
        self.mfa_nb_attempts = 0
        self.save()

    def save(self, force_insert=False, force_update=False,
             using=None, update_fields=None):
        if not self.lang:
            self.lang = settings.LANGUAGE_CODE
        if self.slug: # serializer will set created slug to '' instead of None.
            return super(Contact, self).save(
                force_insert=force_insert, force_update=force_update,
                using=using, update_fields=update_fields)
        max_length = self._meta.get_field('slug').max_length
        slug_base = (self.user.username
            if self.user else slugify(self.email.split('@')[0]))
        if not slug_base:
            # email might be empty
            slug_base = generate_random_slug(15)
        elif len(slug_base) > max_length:
            slug_base = slug_base[:max_length]
        self.slug = slug_base
        for idx in range(1, 10): #pylint:disable=unused-variable
            try:
                try:
                    with transaction.atomic():
                        if self.user:
                            # pylint:disable=unused-variable
                            save_user = False
                            first_name, mid_name, last_name = \
                                full_name_natural_split(self.full_name)
                            if not self.user.first_name:
                                self.user.first_name = first_name
                                save_user = True
                            if not self.user.last_name:
                                self.user.last_name = last_name
                                save_user = True
                            if not self.user.email and self.email:
                                self.user.email = self.email
                                save_user = True
                            if save_user:
                                self.user.save()
                        return super(Contact, self).save(
                            force_insert=force_insert,
                            force_update=force_update,
                            using=using, update_fields=update_fields)
                except IntegrityError as err:
                    if 'uniq' not in str(err).lower():
                        raise
                    handle_uniq_error(err)
            except DRFValidationError as err:
                if not 'slug' in err.detail:
                    raise err
                if len(slug_base) + 8 > max_length:
                    slug_base = slug_base[:(max_length - 8)]
                self.slug = generate_random_slug(
                    length=len(slug_base) + 8, prefix=slug_base + '-')
        raise DRFValidationError({'slug':
            _("Unable to create a unique URL slug with a base of '%(base)s'")
                % {'base': slug_base}})


@python_2_unicode_compatible
class Activity(models.Model):
    """
    Activity associated to a contact.
    """
    created_at = models.DateTimeField(auto_now_add=True,
        help_text=_("Date/time of creation (in ISO format)"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL,
        null=True, on_delete=models.SET_NULL,
        help_text=_("User that created the activity"))
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE)
    text = models.TextField(blank=True,
        help_text=_("Free form text description of the activity"))
    account = models.ForeignKey(
        settings.ACCOUNT_MODEL, null=True, on_delete=models.CASCADE,
        related_name='activities',
        help_text=_("Account the activity is associated to"))
    extra = _get_extra_field_class()(null=True)

    def __str__(self):
        return "%s-%s" % (self.created_at, self.created_by)


@python_2_unicode_compatible
class Notification(models.Model):
    """
    Notification model, represent a single notification type,
    has a M2M relation with users, which allows to store a user's
    email notifications preferences
    """
    NOTIFICATION_TYPE = settings.NOTIFICATION_TYPE

    slug = models.SlugField(unique=True, choices=NOTIFICATION_TYPE,
        help_text=_("Unique identifier shown in the URL bar"))
    title = models.CharField(max_length=100, blank=True)
    description = models.TextField(null=True, blank=True)
    users = models.ManyToManyField(settings.AUTH_USER_MODEL,
        related_name='notifications')
    extra = _get_extra_field_class()(null=True)

    def __str__(self):
        return str(self.slug)


@python_2_unicode_compatible
class Credentials(models.Model):
    """
    API Credentials to authenticate a `User`.
    """
    API_PUB_KEY_LENGTH = 32
    API_PRIV_KEY_LENGTH = 32

    api_pub_key = models.SlugField(unique=True, max_length=API_PUB_KEY_LENGTH)
    api_priv_key = models.CharField(max_length=128)
    user = models.OneToOneField(settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE, related_name='credentials')
    extra = _get_extra_field_class()(null=True)

    def __str__(self):
        return str(self.api_pub_key)

    def set_priv_key(self, api_priv_key):
        self.api_priv_key = make_password(api_priv_key)
        self._api_priv_key = api_priv_key

    def check_priv_key(self, raw_api_priv_key):
        """
        Return a boolean of whether the raw api_priv_key was correct. Handles
        hashing formats behind the scenes.
        """
        def setter(raw_api_priv_key):
            self.set_priv_key(raw_api_priv_key)
            # Password hash upgrades shouldn't be considered password changes.
            self._api_priv_key = None
            self.save(update_fields=["api_priv_key"])
        return check_password(raw_api_priv_key, self.api_priv_key, setter)


@python_2_unicode_compatible
class DelegateAuth(models.Model):
    """
    Authentication for users with e-mail addresses in these domains must be
    delegated to a SSO provider.
    """
    domain = models.CharField(max_length=100, unique=True,
        validators=[domain_name_validator, RegexValidator(URLValidator.host_re,
            _("Enter a valid 'domain', ex: example.com"), 'invalid')],
        help_text=_(
            _("fully qualified domain name at which the site is available")))
    provider = models.CharField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True,
        help_text=_("Date/time of creation (in ISO format)"))


def get_user_contact(user):
    if isinstance(settings.USER_CONTACT_CALLABLE, six.string_types):
        return import_string(settings.USER_CONTACT_CALLABLE)(user)
    if user:
        return Contact.objects.filter(
            models.Q(slug=user.username)
            | models.Q(email=user.email)).order_by(
            'slug', 'email').first()
    return None


# Hack to install our create_user method.
User = get_user_model() #pylint:disable=invalid-name
User.objects = ActivatedUserManager()
User.objects.model = User
User.objects.contribute_to_class(User, 'objects')
