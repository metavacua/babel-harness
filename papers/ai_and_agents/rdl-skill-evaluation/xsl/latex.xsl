<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
  xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
  xmlns:db="http://docbook.org/ns/docbook"
  xmlns:xlink="http://www.w3.org/1999/xlink"
  exclude-result-prefixes="db xlink">

  <xsl:output method="text" encoding="UTF-8"/>
  <xsl:strip-space elements="*"/>

  <xsl:template match="/db:article">
\documentclass[11pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{microtype}
\usepackage{hyperref}
\usepackage{booktabs}
\usepackage{listings}
\usepackage{xcolor}
\usepackage{mdframed}
\usepackage[style=numeric,sorting=none]{biblatex}
\addbibresource{bibliography.bib}

\definecolor{confirmed}{RGB}{40,167,69}
\definecolor{caveats}{RGB}{0,123,255}

\lstset{basicstyle=\small\ttfamily,breaklines=true,frame=single,
        backgroundcolor=\color{gray!10}}

\title{<xsl:apply-templates select="db:title"/>}
\author{metavacua}
\date{2026-06-29}

\begin{document}
\maketitle
\tableofcontents
\newpage

    <xsl:apply-templates select="db:section|db:bibliography"/>

\printbibliography
\end{document}
  </xsl:template>

  <xsl:template match="db:section[count(ancestor::db:section)=0]">
\section{<xsl:value-of select="db:title"/>}
    <xsl:if test="@role='finding'">
      <xsl:choose>
        <xsl:when test="@condition='confirmed'">\begin{mdframed}[linecolor=confirmed,linewidth=2pt]&#10;</xsl:when>
        <xsl:when test="@condition='confirmed-with-caveats'">\begin{mdframed}[linecolor=caveats,linewidth=2pt]&#10;</xsl:when>
        <xsl:otherwise>\begin{mdframed}&#10;</xsl:otherwise>
      </xsl:choose>
    </xsl:if>
    <xsl:apply-templates select="*[not(self::db:title) and not(self::db:section)]"/>
    <xsl:apply-templates select="db:section"/>
    <xsl:if test="@role='finding'">\end{mdframed}&#10;</xsl:if>
  </xsl:template>

  <xsl:template match="db:section[count(ancestor::db:section)=1]">
\subsection{<xsl:value-of select="db:title"/>}
    <xsl:apply-templates select="*[not(self::db:title)]"/>
  </xsl:template>

  <xsl:template match="db:section[count(ancestor::db:section)=2]">
\subsubsection{<xsl:value-of select="db:title"/>}
    <xsl:apply-templates select="*[not(self::db:title)]"/>
  </xsl:template>

  <xsl:template match="db:para">&#10;<xsl:apply-templates/>&#10;</xsl:template>
  <xsl:template match="db:programlisting">
\begin{lstlisting}
<xsl:value-of select="."/>
\end{lstlisting}
  </xsl:template>
  <xsl:template match="db:itemizedlist">
\begin{itemize}
    <xsl:apply-templates/>
\end{itemize}
  </xsl:template>
  <xsl:template match="db:orderedlist">
\begin{enumerate}
    <xsl:apply-templates/>
\end{enumerate}
  </xsl:template>
  <xsl:template match="db:listitem">\item <xsl:apply-templates/>&#10;</xsl:template>
  <xsl:template match="db:quote">``<xsl:apply-templates/>''</xsl:template>

  <xsl:template match="db:emphasis[@role='strong']">\textbf{<xsl:apply-templates/>}</xsl:template>
  <xsl:template match="db:emphasis">\emph{<xsl:apply-templates/>}</xsl:template>
  <xsl:template match="db:literal|db:code|db:function|db:varname|db:filename|db:command|db:replaceable">
    <xsl:text>\texttt{</xsl:text><xsl:apply-templates/><xsl:text>}</xsl:text>
  </xsl:template>
  <xsl:template match="db:link[@xlink:href]">
    <xsl:apply-templates/> (\url{<xsl:value-of select="@xlink:href"/>})
  </xsl:template>
  <xsl:template match="db:citation">\cite{<xsl:value-of select="."/>}</xsl:template>

  <xsl:template match="db:informaltable">
\begin{table}[h]\centering\begin{tabular}{lll}\toprule
    <xsl:apply-templates select=".//db:thead/db:row"/>
\midrule
    <xsl:apply-templates select=".//db:tbody/db:row"/>
\bottomrule\end{tabular}\end{table}
  </xsl:template>
  <xsl:template match="db:row">
    <xsl:for-each select="db:entry">
      <xsl:if test="position() > 1"> &amp; </xsl:if>
      <xsl:apply-templates/>
    </xsl:for-each>\\&#10;
  </xsl:template>
  <xsl:template match="db:tgroup|db:colspec|db:thead|db:tbody"/>
  <xsl:template match="db:bibliography"/>
  <xsl:template match="db:info"/>

</xsl:stylesheet>
