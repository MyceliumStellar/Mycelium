import React from "react";
import DocsContent from "../docs-content";

interface PageProps {
  params: Promise<{ slug: string }>;
}

export default async function Page({ params }: PageProps) {
  const { slug } = await params;
  return <DocsContent slug={slug} />;
}
