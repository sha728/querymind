SELECT count(*) FROM customers	mini
SELECT name FROM products ORDER BY price DESC	mini
SELECT customer_id, sum(amount) FROM orders GROUP BY customer_id	mini
SELECT name FROM products WHERE discontinued = 1	mini
SELECT sum(quantity) FROM order_items WHERE order_id = 10248	mini
