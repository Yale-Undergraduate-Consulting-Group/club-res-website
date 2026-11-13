# Northwind Grocers: Loyalty Pricing Review

**Term:** Fall 2022 | **Team:** 5 consultants, 1 faculty advisor | **Industry:** Retail (grocery)

> Fictional case written for the sample data set. Northwind Grocers is not a real company.

## Situation

Northwind Grocers is a regional chain of 42 supermarkets. Loyalty members drive 71% of sales, yet the
chain gives every member the same weekly coupon book. Margins have slipped 130 basis points in two
years as national competitors pushed deeper promotions on staples, and the CFO asked whether Northwind
was "buying traffic it would have had anyway."

## Our approach

1. Rebuilt the pricing baseline from 18 months of basket data (about 9.4 million loyalty transactions).
2. Estimated price elasticity for 31 categories using store-level price tests that the chain had already run.
3. Segmented loyalty members by price sensitivity and basket composition (stock-up, fill-in, convenience).
4. Modelled three pricing scenarios against margin, traffic and private-label share.

## Key findings

- **Promotions are shallow where shoppers are inelastic.** Dairy, eggs and fresh produce showed low
  price elasticity (-0.4 to -0.7), yet carried the deepest weekly discounts.
- **Private label is under-priced.** Northwind's own brand sold at a 38% discount to national brands in
  categories where shoppers rated quality as equal, leaving roughly 6 points of margin on the table.
- **Three segments behave very differently.** Stock-up shoppers (22% of members) respond to multi-buy
  offers, while convenience shoppers (41%) almost never redeem coupons.
- **Coupon redemption is concentrated.** 8% of members redeem 55% of coupon value.

## Recommendations

1. Cut promotional depth by a third on the 12 least-elastic categories and reinvest in sharper entry-price
   items (milk, bananas, bread) that anchor price perception.
2. Move private label to a good-better-best ladder and raise the "good" tier price by 4% to 6%.
3. Replace the single coupon book with segment-targeted offers delivered in the app.
4. Run a 12-week controlled pilot in six stores before chain-wide rollout.

## Expected impact

Modelled margin improvement of 90 to 120 basis points at flat unit volume, with a downside case of
50 basis points if competitors respond within a quarter. The pilot design in the appendix was built to
detect a 40 basis point effect with 80% power.

## Lessons for future teams

- Get the transaction extract in week one; it took five weeks of data cleaning to get usable.
- Elasticity numbers invite debate. Present ranges and show the store-test evidence behind each one.
