<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:db="http://docbook.org/ns/docbook"
  xmlns:dc="http://purl.org/dc/terms/"
  exclude-result-prefixes="db dc">

  <xsl:output method="html" version="5" encoding="UTF-8" indent="yes"/>

  <!-- ── Root ──────────────────────────────────────────────────────────────── -->
  <xsl:template match="/db:article">
    <xsl:text disable-output-escaping="yes">&lt;!DOCTYPE html&gt;&#10;</xsl:text>
    <html lang="{@xml:lang}">
      <head>
        <meta charset="UTF-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title><xsl:value-of select="db:title"/></title>
        <xsl:apply-templates select="db:info/dc:*" mode="meta"/>
        <xsl:apply-templates select="db:info/db:bibliomisc[@role='schema-org-jsonld']"/>
        <style>
          body { font-family: Georgia, serif; max-width: 52em; margin: 2em auto; padding: 0 1em; line-height: 1.6; }
          h1 { font-size: 1.6em; }
          h2 { font-size: 1.3em; border-bottom: 1px solid #ccc; padding-bottom: .2em; }
          h3 { font-size: 1.1em; }
          pre { background: #f4f4f4; padding: 1em; overflow-x: auto; font-size: .88em; }
          table { border-collapse: collapse; margin: 1em 0; }
          th, td { border: 1px solid #ccc; padding: .4em .8em; }
          th { background: #f0f0f0; }
          section[data-condition="confirmed"]               { border-left: 4px solid #2a2; padding-left: 1em; }
          section[data-condition="confirmed-with-caveats"]  { border-left: 4px solid #22a; padding-left: 1em; }
          section[data-condition="split"]                   { border-left: 4px solid #a80; padding-left: 1em; }
          .finding-badge { font-size: .75em; font-weight: bold; text-transform: uppercase;
                           padding: .1em .4em; border-radius: 3px; margin-left: .5em; }
          .confirmed              { background: #d4edda; color: #155724; }
          .confirmed-with-caveats { background: #cce5ff; color: #004085; }
          .split                  { background: #fff3cd; color: #856404; }
          blockquote { border-left: 3px solid #aaa; margin-left: 0; padding-left: 1em; color: #555; }
        </style>
      </head>
      <body>
        <h1><xsl:value-of select="db:title"/></h1>
        <xsl:apply-templates select="db:section|db:bibliography"/>
      </body>
    </html>
  </xsl:template>

  <xsl:template match="dc:*" mode="meta">
    <meta name="DC.{local-name()}" content="{.}"/>
  </xsl:template>

  <xsl:template match="db:bibliomisc[@role='schema-org-jsonld']">
    <script type="application/ld+json"><xsl:value-of select="."/></script>
  </xsl:template>

  <xsl:template match="db:section">
    <section>
      <xsl:if test="@xml:id"><xsl:attribute name="id"><xsl:value-of select="@xml:id"/></xsl:attribute></xsl:if>
      <xsl:if test="@condition"><xsl:attribute name="data-condition"><xsl:value-of select="@condition"/></xsl:attribute></xsl:if>
      <xsl:apply-templates/>
    </section>
  </xsl:template>

  <xsl:template match="db:section/db:title">
    <xsl:variable name="depth" select="count(ancestor::db:section)"/>
    <xsl:choose>
      <xsl:when test="$depth = 1"><h2><xsl:apply-templates/><xsl:call-template name="finding-badge"/></h2></xsl:when>
      <xsl:when test="$depth = 2"><h3><xsl:apply-templates/><xsl:call-template name="finding-badge"/></h3></xsl:when>
      <xsl:otherwise><h4><xsl:apply-templates/></h4></xsl:otherwise>
    </xsl:choose>
  </xsl:template>

  <xsl:template name="finding-badge">
    <xsl:if test="parent::db:section/@role = 'finding'">
      <xsl:variable name="cond" select="parent::db:section/@condition"/>
      <span class="finding-badge {$cond}"><xsl:value-of select="$cond"/></span>
    </xsl:if>
  </xsl:template>

  <xsl:template match="db:para"><p><xsl:apply-templates/></p></xsl:template>
  <xsl:template match="db:programlisting"><pre><code><xsl:apply-templates/></code></pre></xsl:template>
  <xsl:template match="db:itemizedlist"><ul><xsl:apply-templates/></ul></xsl:template>
  <xsl:template match="db:orderedlist"><ol><xsl:apply-templates/></ol></xsl:template>
  <xsl:template match="db:listitem"><li><xsl:apply-templates/></li></xsl:template>
  <xsl:template match="db:quote"><blockquote><xsl:apply-templates/></blockquote></xsl:template>

  <xsl:template match="db:emphasis[@role='strong']"><strong><xsl:apply-templates/></strong></xsl:template>
  <xsl:template match="db:emphasis"><em><xsl:apply-templates/></em></xsl:template>
  <xsl:template match="db:literal|db:code|db:function|db:varname|db:filename|db:command|db:replaceable">
    <code><xsl:apply-templates/></code>
  </xsl:template>
  <xsl:template match="db:link[@xlink:href]">
    <a href="{@xlink:href}"><xsl:apply-templates/></a>
  </xsl:template>
  <xsl:template match="db:citation">[<xsl:value-of select="."/>]</xsl:template>

  <xsl:template match="db:informaltable"><table><xsl:apply-templates/></table></xsl:template>
  <xsl:template match="db:tgroup"><xsl:apply-templates/></xsl:template>
  <xsl:template match="db:thead"><thead><xsl:apply-templates/></thead></xsl:template>
  <xsl:template match="db:tbody"><tbody><xsl:apply-templates/></tbody></xsl:template>
  <xsl:template match="db:row"><tr><xsl:apply-templates/></tr></xsl:template>
  <xsl:template match="db:entry[parent::db:row[parent::db:thead]]"><th><xsl:apply-templates/></th></xsl:template>
  <xsl:template match="db:entry"><td><xsl:apply-templates/></td></xsl:template>
  <xsl:template match="db:colspec"/>

  <xsl:template match="db:bibliography">
    <section id="references">
      <h2><xsl:value-of select="db:title"/></h2>
      <xsl:apply-templates select="db:bibliodiv"/>
    </section>
  </xsl:template>
  <xsl:template match="db:bibliodiv"><xsl:apply-templates/></xsl:template>
  <xsl:template match="db:bibliomixed">
    <p id="{@xml:id}"><xsl:apply-templates/></p>
  </xsl:template>
  <xsl:template match="db:bibliography/db:title"/>

</xsl:stylesheet>
