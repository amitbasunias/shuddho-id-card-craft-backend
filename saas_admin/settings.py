"""
Django settings for saas_admin project.

This is the ID Craft SaaS admin layer: a standalone Django service that manages
subscribers, subscription plans, billing periods and payments for the FastAPI
product. It keeps its own database and does not touch the product's data.
"""

import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
# Reads from the environment in production; falls back to a dev key locally.
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-=%g9%l9-c@sh7rkjyeh3gy*9=_n=aj68s6_^mb-sc)bzfos@o^',
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('1', 'true', 'yes')

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1', 'https://shuddhoapp-7wyg9.ondigitalocean.app').split(',')
    if h.strip()
]
# DEV-ONLY: tunnel via ngrok sends every Host header (random *.ngrok-free.app).
# Wildcard ALLOWED_HOSTS while DEBUG=True so we don't have to chase URLs.
if DEBUG:
    ALLOWED_HOSTS = ['*']


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    # Local apps
    'subscribers',
    'product',
    'accounts',
    'business',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'saas_admin.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'saas_admin.wsgi.application'


# Database
# https://docs.djangoproject.com/en/5.1/ref/settings/#databases

# Django owns the entire schema in a single database (one backend, one DB).
# Default is a local sqlite file; SAAS_DB_PATH overrides the location, and you
# can swap ENGINE to PostgreSQL in production via env without code changes.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ.get('SAAS_DB_PATH', str(BASE_DIR / 'db.sqlite3')),
    }
}


# Password validation
# https://docs.djangoproject.com/en/5.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

# Product is used in Bangladesh (Bengali fonts / Bijoy conversion in the
# product). Billing periods and due dates are interpreted in local time.
TIME_ZONE = 'Asia/Dhaka'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.1/howto/static-files/

STATIC_URL = 'static/'

# Default primary key field type
# https://docs.djangoproject.com/en/5.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ---------------------------------------------------------------------------
# SaaS / billing configuration
# ---------------------------------------------------------------------------

# Default currency for plans and invoices (BDT for Bangladesh). Display only.
SAAS_DEFAULT_CURRENCY = os.environ.get('SAAS_DEFAULT_CURRENCY', 'BDT')

# How many days before a billing period ends to flag an account as "due soon"
# in the admin (purely a visual cue for manual billing).
SAAS_DUE_SOON_DAYS = int(os.environ.get('SAAS_DUE_SOON_DAYS', '7'))

# Admin site branding.
SAAS_SITE_NAME = os.environ.get('SAAS_SITE_NAME', 'ID Craft SaaS')

# Mobile-banking merchant numbers shown to subscribers on the package-payment
# screen. Plain strings, env-configured so each deployment can publish its own.
PAY_BKASH_NUMBER = os.environ.get('PAY_BKASH_NUMBER', '01318676267')
PAY_BKASH_TYPE = os.environ.get('PAY_BKASH_TYPE', 'Send Money')
PAY_NAGAD_NUMBER = os.environ.get('PAY_NAGAD_NUMBER', '01318676267')
PAY_NAGAD_TYPE = os.environ.get('PAY_NAGAD_TYPE', 'Send Money')
PAY_INSTRUCTIONS = os.environ.get(
    'PAY_INSTRUCTIONS',
    'Send the exact amount, then submit the bKash/Nagad transaction id below.'
)

# ---------------------------------------------------------------------------
# Django REST Framework + JWT
# ---------------------------------------------------------------------------
from datetime import timedelta  # noqa: E402

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_RENDERER_CLASSES": (
        "rest_framework.renderers.JSONRenderer",
    ),
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# CORS - allow the React frontends (dev + configurable prod origins).
CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173",
    ).split(",")
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True

# DEV-ONLY: open CORS so we can hit the API from random ngrok hostnames
# (or curl from anywhere) without listing each one. Pair this with the
# wildcard ALLOWED_HOSTS above. NOTE: when CORS_ALLOW_ALL_ORIGINS is True the
# corsheaders middleware does NOT echo credentials back, so we also stop
# requiring credentials in dev — JWTs travel in the Authorization header
# anyway, not in cookies.
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOW_CREDENTIALS = False

# DEV-ONLY: same idea for Django's CSRF host check (used by /admin POSTs).
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:8000", "https://shuddhoapp-7wyg9.ondigitalocean.app"
    ).split(",")
    if o.strip()
]
if DEBUG:
    # Accept any https://*.ngrok-free.app or .ngrok.app, plus the http variants.
    CSRF_TRUSTED_ORIGINS += [
        "https://*.ngrok-free.app", "http://*.ngrok-free.app",
        "https://*.ngrok.app",      "http://*.ngrok.app",
        "https://*.ngrok.io",       "http://*.ngrok.io","https://shuddhoapp-7wyg9.ondigitalocean.app"
    ]

# Storage roots for the product (uploads / generated outputs / templates / fonts).
PRODUCT_STORAGE_ROOT = os.environ.get(
    "PRODUCT_STORAGE_ROOT", str(BASE_DIR / "product_storage")
)

# Email backend (console for now; swap to SMTP later via env).
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_FROM_EMAIL", "noreply@idcraft.local")
