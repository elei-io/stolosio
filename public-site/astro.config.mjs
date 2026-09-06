import { defineConfig } from "astro/config";
import starlight from "@astrojs/starlight";

export default defineConfig({
  integrations: [
    starlight({
      title: "stolosio",
      description:
        "Deploy and operate your own browser and web-acquisition gateway.",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/elei-io/stolosio",
        },
      ],
      customCss: ["./src/styles/docs.css"],
      sidebar: [
        {
          label: "Start here",
          items: [
            { label: "Introduction", slug: "docs" },
            { label: "Quickstart", slug: "docs/quickstart" },
            { label: "Connect your clients", slug: "docs/clients" },
          ],
        },
        {
          label: "Deploy",
          items: [
            { label: "Kubernetes & k3s", slug: "docs/kubernetes" },
            { label: "Security & networking", slug: "docs/security" },
          ],
        },
        {
          label: "Operate",
          items: [
            { label: "Fleet & routing", slug: "docs/operations" },
            { label: "Observe & troubleshoot", slug: "docs/observability" },
            { label: "Maintenance & upgrades", slug: "docs/maintenance" },
          ],
        },
        {
          label: "Reference",
          items: [
            { label: "Providers & compatibility", slug: "docs/providers" },
            { label: "Configuration", slug: "docs/configuration" },
            { label: "Architecture", slug: "docs/architecture" },
          ],
        },
      ],
    }),
  ],
});
