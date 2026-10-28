from __future__ import annotations

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from . import services


def _unique_slug(model, base: str, pk=None) -> str:
    base = slugify(base, allow_unicode=True)[:140] or "post"
    slug, n = base, 2
    while model.objects.filter(slug=slug).exclude(pk=pk).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=100, unique=True, allow_unicode=True, blank=True)
    description = models.CharField(max_length=200, blank=True, help_text="Shown at the top of the category page")
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ("position", "name")
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.slug = self.slug or _unique_slug(Category, self.name, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("blog:category", args=[self.slug])


class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=80, unique=True, allow_unicode=True, blank=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.slug = self.slug or _unique_slug(Tag, self.name, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("blog:tag", args=[self.slug])


class PostQuerySet(models.QuerySet):
    def published(self):
        return self.filter(is_published=True, published_at__lte=timezone.now())


class Post(models.Model):
    """A blog article. The author is always the shop itself, so there is no author field."""

    title = models.CharField(max_length=160)
    slug = models.SlugField(max_length=160, unique=True, allow_unicode=True, blank=True, help_text="The web address, for example best-chain-handbags-bangladesh. Filled in from the title when empty.")
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, related_name="posts")
    tags = models.ManyToManyField(Tag, blank=True, related_name="posts")
    body = models.TextField(help_text="Use the editor: headings, lists, tables, links and the image button (uploads are saved on this site).")
    cover_image = models.ImageField(upload_to="blog/", blank=True, help_text="Shown on the blog list, at the top of the article and when the link is shared")
    cover_alt = models.CharField("cover image description", max_length=160, blank=True, help_text="Describe the picture in a few words (helps search engines and screen readers)")
    seo_title = models.CharField("SEO title", max_length=70, blank=True, help_text="Optional. The title shown in Google; the article title is used when empty")
    meta_description = models.CharField(max_length=160, blank=True, help_text="One or two sentences shown under the title in Google")
    excerpt = models.CharField(max_length=240, blank=True, help_text="Optional short summary for the blog list; the meta description is used when empty")
    related_products = models.ManyToManyField("catalog.Product", blank=True, related_name="blog_posts", help_text="Shown as a \"shop these products\" box at the end of the article")
    is_published = models.BooleanField(default=False, help_text="Untick to keep it as a draft")
    published_at = models.DateTimeField(null=True, blank=True, help_text="Publishing date and time. Leave empty to publish now; a future time schedules it")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PostQuerySet.as_manager()

    class Meta:
        ordering = ("-published_at", "-created_at")

    def __str__(self) -> str:
        return self.title

    @property
    def author_name(self) -> str:
        return settings.SITE_NAME

    @property
    def heading(self) -> str:
        return self.seo_title or self.title

    @property
    def summary(self) -> str:
        return self.excerpt or self.meta_description or services.plain_text(self.body)[:160]

    @property
    def reading_minutes(self) -> int:
        return services.reading_minutes(self.body)

    @property
    def is_live(self) -> bool:
        return bool(self.is_published and self.published_at and self.published_at <= timezone.now())

    def save(self, *args, **kwargs):
        self.slug = self.slug or _unique_slug(Post, self.title, self.pk)
        self.body = services.clean_article_html(self.body)
        if self.is_published and not self.published_at:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("blog:post", args=[self.slug])
