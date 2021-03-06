from django.db import DatabaseError

from users.tests import BaseUserTestCase

from .models import Chapter, Comment, Post


class BasePostTestCase(BaseUserTestCase):
    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(author=self.main_user)

    def tearDown(self):
        super().tearDown()

        try:
            if self.post.id is not None:
                self.post.delete()
        except DatabaseError:
            pass


class PublishedPostTestCase(BasePostTestCase):
    def setUp(self):
        super().setUp()
        Chapter.objects.create(
            post=self.post, position=self.post.chapter_position(0), text="Text"
        )
        self.post.publish(anonymous=False)


class BaseCommentTestCase(PublishedPostTestCase):
    def setUp(self):
        super().setUp()
        self.comment = Comment.objects.create(
            post=self.post, author=self.other_user, text="Text"
        )
