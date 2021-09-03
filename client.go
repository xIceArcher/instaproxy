package main

import (
	"context"
	"net/http"

	"golang.org/x/time/rate"
)

type RateLimitedHTTPClient struct {
	RateLimiter *rate.Limiter

	client *http.Client
}

func (c *RateLimitedHTTPClient) Do(req *http.Request) (*http.Response, error) {
	ctx := context.Background()
	if err := c.RateLimiter.Wait(ctx); err != nil {
		return nil, err
	}

	return c.client.Do(req)
}

func NewRateLimitedHTTPClient(c *http.Client, r *rate.Limiter) *RateLimitedHTTPClient {
	return &RateLimitedHTTPClient{
		client:      c,
		RateLimiter: r,
	}
}
