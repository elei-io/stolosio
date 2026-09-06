import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, existsSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { load } from "cheerio";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../dist");
const walk = (dir) =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
const pages = walk(root).filter((path) => path.endsWith(".html"));

test("all built pages have metadata and valid internal links and anchors", () => {
  assert.ok(pages.length >= 13, "Homepage, docs, and 404 must be built");
  for (const file of pages) {
    const $ = load(readFileSync(file, "utf8"));
    assert.ok($("title").text().trim(), `${file}: title`);
    assert.ok(
      $('meta[name="description"]').attr("content"),
      `${file}: description`,
    );
    assert.equal($("h1").length, 1, `${file}: one h1`);
    const path = "/" + file.slice(root.length + 1).replace(/index\.html$/, "");
    for (const el of $("a[href]").toArray()) {
      const href = $(el).attr("href");
      const url = new URL(href, "https://local.test" + path);
      if (url.origin !== "https://local.test") continue;
      const target = join(root, decodeURIComponent(url.pathname));
      const page = target.endsWith(".html")
        ? target
        : join(target, "index.html");
      assert.ok(existsSync(page), `${path}: broken link ${href}`);
      if (url.hash) {
        const doc = load(readFileSync(page, "utf8"));
        const id = decodeURIComponent(url.hash.slice(1));
        assert.ok(
          doc("[id]")
            .toArray()
            .some((node) => doc(node).attr("id") === id),
          `${path}: missing anchor ${href}`,
        );
      }
    }
  }
});

test("search index and static homepage assets are generated", () => {
  assert.ok(existsSync(join(root, "pagefind/pagefind.js")));
  const $ = load(readFileSync(join(root, "index.html"), "utf8"));
  for (const el of $('link[rel="stylesheet"], script[src]').toArray()) {
    const url = $(el).attr("href") ?? $(el).attr("src");
    if (url?.startsWith("/"))
      assert.ok(existsSync(join(root, url)), `Missing asset ${url}`);
  }
  assert.ok(
    $("#example-code").text().includes("ws://localhost:8411/v1/connect"),
  );
});
