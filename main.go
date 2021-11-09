package main

import (
	"context"
	"io/ioutil"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/anaskhan96/soup"
	"github.com/go-redis/cache/v8"
	"github.com/go-redis/redis/v8"
	"github.com/gorilla/mux"
	"golang.org/x/time/rate"
)

var baseURL string = "https://instagram.com/p/"
var httpClient *RateLimitedHTTPClient = &RateLimitedHTTPClient{
	client: &http.Client{
		Timeout: 10 * time.Second,
	},
	RateLimiter: rate.NewLimiter(rate.Every(time.Second), 1),
}

var redisContext context.Context = context.Background()
var redisCache *cache.Cache = cache.New(&cache.Options{
	Redis: redis.NewClient(&redis.Options{
		Addr:     "localhost:6379",
		Password: "",
		DB:       0,
	}),
	LocalCache: cache.NewTinyLFU(1000, time.Minute),
})

func requestHandler(w http.ResponseWriter, r *http.Request) {
	postID := mux.Vars(r)["postID"]
	w.Header().Set("Content-Type", "application/json")

	var sharedData string
	if redisCache.Exists(redisContext, postID) {
		if err := redisCache.Get(redisContext, postID, &sharedData); err != nil {
			// Cannot read cache for some reason, just continue
		} else {
			log.Println("Cache hit: " + postID)
			w.Write([]byte(sharedData))
			return
		}
	}

	req, err := http.NewRequest("GET", baseURL+postID, nil)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")

	resp, err := httpClient.Do(req)
	if err != nil {
		http.Error(w, err.Error(), http.StatusNotFound)
		return
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		http.Error(w, resp.Status, resp.StatusCode)
		return
	}

	body, err := ioutil.ReadAll(resp.Body)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}

	doc := soup.HTMLParse(string(body))
	scripts := doc.FindAll("script", "type", "text/javascript")

	for _, script := range scripts {
		text := script.Text()
		if len(text) > 21 && script.Text()[0:18] == "window._sharedData" {
			sharedData = text[21 : len(text)-1]
			break
		}
	}

	if err = redisCache.Set(&cache.Item{
		Ctx:   redisContext,
		Key:   postID,
		Value: sharedData,
		TTL:   24 * time.Hour,
	}); err != nil {
		// Cannot write cache for some reason, just continue
	}

	log.Println("Cache miss: " + postID)
	w.Write([]byte(sharedData))
}

func main() {
	f, err := os.OpenFile("logs.txt", os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0666)
	if err != nil {
		log.Fatal(err)
	}
	log.SetOutput(f)

	r := mux.NewRouter()
	r.HandleFunc("/instagram/p/{postID}", requestHandler)

	log.Fatal(http.ListenAndServe(":8080", r))
}
