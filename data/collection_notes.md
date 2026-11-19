# Collection provenance

Collected on 18 November 2026 using requests, BeautifulSoup and pypdf:

- https://cdn.darglobal.co.uk/Aida_Phase_01_EN_8e90a16291.pdf
- https://cdn.darglobal.co.uk/DG_AIDA_Trump_Brochure_EN_6a6b1c7469.pdf
- https://wasalt.sa/en
- https://wasalt.sa/en/corporate
- https://wasalt.sa/en/apartments-for-sale-in-riyadh

knowledge.txt contains 85 text-bearing PDF page blocks from the official DarGlobal brochures and 50 Wasalt HTML pages, covering services, sale/rental results in multiple cities and sample individual listings. Each PDF block links to its original document and page number. Image-only or very short PDF pages are skipped. The original brochures describe projects as marketed at publication time; collection date does not imply that prices, timelines or availability are current. All earlier research-note blocks were replaced by directly extracted text.

See scrape_report.json for counts, restrictions and page-limit status. This is not a complete site crawl. Prices and bedroom/area values are reproduced as presented by the source; unusual values have not been silently corrected. Availability has not been checked.

Initial urllib requests to both sources were blocked. The requests-based crawler collected Wasalt pages successfully, while DarGlobal marketing HTML continued to return Incapsula challenge pages. The official CDN permitted public brochure downloads. The crawler obeys robots.txt and stops after repeated access restrictions. It writes a report of every refresh.

On 19 November 2026, a company-wide coverage gap was corrected with a verified summary of DarGlobal's official corporate release dated 26 October 2026: https://darglobal.co.uk/press/dar-global-capital-partners-targets-high-value-real-estate-opportunities-dubai. The public-page extract reports a US$23 billion global portfolio GDV. Direct HTTP collection remained blocked, so this block is explicitly marked `Method: Verified public-page summary`; it is not presented as a raw HTML scrape. Its publication date is included to distinguish the figure from older project brochures. The scraper retains this summary until it can fetch the same source URL directly.
