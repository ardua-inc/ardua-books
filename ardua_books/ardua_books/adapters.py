import logging
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.exceptions import ImmediateHttpResponse
from django.contrib import messages
from django.shortcuts import redirect
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()

class RestrictToExistingUserAdapter(DefaultSocialAccountAdapter):
    """
    Adapter that prevents new user signups from Google SSO.
    Only allows login if the Google email matches an existing User.
    """

    def pre_social_login(self, request, sociallogin):
        """
        Invoked just after a user successfully authenticates via a social
        provider, but before the login is actually processed.
        """
        # If the user is already logged in, no need to do anything
        if request.user.is_authenticated:
            return

        # If it's a new social account, we check for an existing user matching the email
        if not sociallogin.is_existing:
            email = sociallogin.account.extra_data.get('email', '').lower()

            if not email:
                # Provide a user-friendly error message if no email is provided
                messages.error(request, 'No email address provided by Google.')
                raise ImmediateHttpResponse(redirect('login'))

            try:
                # Check if user with this email exists
                user = User.objects.get(email__iexact=email)
                
                # Link the social account to the existing user
                sociallogin.connect(request, user)
                
            except User.DoesNotExist:
                # Reject login if no existing user matches
                logger.warning(f"Rejected SSO login from unknown email: {email}")
                messages.error(request, 'Your email address is not authorized to access this system. Please contact an administrator.')
                raise ImmediateHttpResponse(redirect('login'))
