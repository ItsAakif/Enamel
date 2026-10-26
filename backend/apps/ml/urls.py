"""ML URL routes (mounted under ``/api/v1/``)."""

from django.urls import path

from .views import ModelListView, PredictionListView, PredictView

app_name = "ml"

urlpatterns = [
    path(
        "ml/no-show/models/",
        ModelListView.as_view(),
        name="noshow-model-list",
    ),
    path(
        "ml/no-show/predict/",
        PredictView.as_view(),
        name="noshow-predict",
    ),
    path(
        "ml/no-show/predictions/",
        PredictionListView.as_view(),
        name="noshow-prediction-list",
    ),
]
