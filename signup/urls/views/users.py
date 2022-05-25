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

from ...settings import USERNAME_PAT
from ...compat import re_path
from ...views.users import (PasswordChangeView, UserPublicKeyUpdateView,
    UserProfileView, UserNotificationsView, redirect_to_user_profile)

urlpatterns = [
    # These three URLs must be protected.
    re_path(r'^(?P<user>%s)/password/' % USERNAME_PAT,
        PasswordChangeView.as_view(), name='password_change'),
    re_path(r'^(?P<user>%s)/pubkey/' % USERNAME_PAT,
        UserPublicKeyUpdateView.as_view(), name='pubkey_update'),
    re_path(r'^(?P<user>%s)/notifications/' % USERNAME_PAT,
        UserNotificationsView.as_view(), name='users_notifications'),
    re_path(r'^(?P<user>%s)/' % USERNAME_PAT,
        UserProfileView.as_view(), name='users_profile'),
    re_path(r'^', redirect_to_user_profile, name='accounts_profile'),
]
