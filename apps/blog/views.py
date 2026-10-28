from django.conf import settings
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, ListView

from apps.catalog.services import card_prefetch
from apps.core import seo_meta

from .models import Category, Post, Tag


class PostListView(ListView):
    template_name = "blog/index.html"
    context_object_name = "posts"
    paginate_by = 9

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.category = self.tag = None

    def get_queryset(self):
        qs = Post.objects.published().select_related("category").prefetch_related("tags")
        slug = self.kwargs.get("slug")
        if slug and self.request.resolver_match.url_name == "category":
            self.category = get_object_or_404(Category, slug=slug)
            qs = qs.filter(category=self.category)
        elif slug:
            self.tag = get_object_or_404(Tag, slug=slug)
            qs = qs.filter(tags=self.tag)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(category=self.category, tag=self.tag, categories=Category.objects.filter(posts__in=Post.objects.published()).distinct())
        return ctx


class PostDetailView(DetailView):
    template_name = "blog/post.html"
    context_object_name = "post"

    def get_queryset(self):
        return Post.objects.published().select_related("category").prefetch_related("tags")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        post = self.object
        url = seo_meta.base_url() + post.get_absolute_url()
        image = seo_meta.image_url(post.cover_image)
        article = {
            "@type": "BlogPosting", "@id": url + "#article", "mainEntityOfPage": {"@id": url + "#webpage"}, "headline": post.heading[:110],
            "description": post.summary, "datePublished": post.published_at.isoformat(), "dateModified": post.updated_at.isoformat(),
            "author": {"@type": "Organization", "name": settings.SITE_NAME, "url": seo_meta.base_url()},
            "publisher": {"@type": "Organization", "name": settings.SITE_NAME, "url": seo_meta.base_url()},
        }
        if image:
            article["image"] = [image]
        if post.category:
            article["articleSection"] = post.category.name
        related = Post.objects.published().exclude(pk=post.pk).select_related("category")
        related = related.filter(category=post.category) if post.category_id else related
        ctx.update(
            seo_image=image, article_ld=[article], related_posts=related[:3],
            products=post.related_products.filter(is_active=True).select_related("category").prefetch_related(*card_prefetch()),
            crumbs=[("Home", "/"), ("Blog", "/blog/"), (post.title, post.get_absolute_url())],
        )
        return ctx
