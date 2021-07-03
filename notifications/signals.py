from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.signals import post_soft_delete
from posts.models import Comment
from posts.signals import fetched
from users.signals import post_ban

from .models import CountUnit, Notification, delete_notifications_for
from .tasks import send_notifications, remove_comments_from_notifications


@receiver(post_soft_delete)
def on_post_soft_delete(instance: Model, **kwargs):
    delete_notifications_for(instance)


@receiver(post_ban, sender=get_user_model())
def on_user_post_ban(instance: AbstractUser, **kwargs):
    delete_notifications_for(instance)


@receiver(post_save, sender=Comment)
def on_comment_post_save(instance: Comment, created: bool, **kwargs):
    if created:
        send_notifications.delay(comment_id=str(instance.id))


@receiver(fetched, sender=Comment)
def on_comment_fetched(user: AbstractUser, pk_set: list, **kwargs):
    remove_comments_from_notifications.delay(
        user_id=str(user.id), comment_ids=list(pk_set)
    )


@receiver(post_save, sender=CountUnit)
def on_count_unit_post_save(instance: CountUnit, **kwargs):
    instance.notification.save()


@receiver(post_delete, sender=CountUnit)
def on_count_unit_post_delete(instance: CountUnit, **kwargs):
    if CountUnit.objects.filter(notification_id=instance.notification_id).count() == 0:
        try:
            instance.notification.delete()
        except Notification.DoesNotExist:
            pass
