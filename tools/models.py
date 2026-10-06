from django.db import models


class user(models.Model):

    name = models.CharField(max_length=100)

    username = models.CharField(
        max_length=100,
        unique=True
    )

    email = models.EmailField(
        unique=True
    )

    password = models.CharField(
        max_length=255
    )

    mobile = models.CharField(
        max_length=15
    )

    def __str__(self):
        return self.username