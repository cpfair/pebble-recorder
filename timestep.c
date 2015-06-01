// Based on https://github.com/vi/timeskew
#define _GNU_SOURCE 1
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/types.h>
#include <dlfcn.h>
#include <sys/time.h>
#include <sys/select.h>
#include <time.h>
#include <signal.h>

#define min(a,b) \
   ({ __typeof__ (a) _a = (a); \
       __typeof__ (b) _b = (b); \
     _a < _b ? _a : _b; })

#define SIG_FREEZETIME SIGURG
#define SIG_STEPTIME SIGUSR2

static int (*orig_gettimeofday)(struct timeval*, struct timezone*) = NULL;

static struct timespec timebase_monotonic;
static struct timespec timebase_realtime;
static struct timeval timebase_gettimeofday;

struct tiacc {
    long long int lastsysval;
    long long int lastourval;
};

static struct tiacc accumulators[3] = {{0,0}, {0,0}, {0,0}};

static int num = 1;
static int denom = 10;
static int timestep = 1;
static int timestep_idle = 1;
static long long int shift = 0;
static char time_is_frozen = 0;
static int pending_step = 0;
static int driver_pid = -1;

static const int max_intercall_delta = 20000; // ns, = 200us, approx 2x realtime based on my measurements

#define MAINT_PERIOD 1024
static int maint_counter=0;

static void sigusr_handler(int signo)
{
  if (signo == SIG_STEPTIME) {
    
      
    // printf("Stepping time %d now %lldus + %llds\n", timestep, increment_us, increment_s);
    // accumulators[1].lastourval += timestep * 1000LL;
    // We can't just bump the clock by `timestep` because the RTC throws a fit
    // (normally it sees a ~10000ns delta between gettimeofday calls)
    // So, we need to fast-forward the clock bit by bit
    // (implemented in filter_time)
    pending_step += timestep;
    // printf("Stepping time %dns, pending %dns\n", timestep, pending_step);
  } else if (signo == SIG_FREEZETIME) {
    // Freeze/unfreeze time
    if (time_is_frozen) {
        time_is_frozen = 0;
        driver_pid = -1;
        printf("Stopped freezing time\n");
    } else {
        time_is_frozen = 1;
        // load params
        if(getenv("DRIVER_PARAMS")) {
            FILE* f=fopen(getenv("DRIVER_PARAMS"), "r");
            if(f) {
                fscanf(f, "%i%i%i", &timestep, &timestep_idle, &driver_pid);
                fclose(f);
            }
        }
        printf("Started freezing time, driver=%d, TS=%d, TS Idle=%d\n", driver_pid, timestep, timestep_idle);
    }
  }
}

static void maint() {
    if (maint_counter==0) {
        if (signal(SIG_STEPTIME, sigusr_handler) == SIG_ERR){
            printf("Failed to attach to SIG_STEPTIME! Time won't be steppable\n");
        }
        if (signal(SIG_FREEZETIME, sigusr_handler) == SIG_ERR){
            printf("Failed to attach to SIG_FREEZETIME! Time will be freezable\n");
        }

        signal(SIGINT, SIG_IGN);

        printf("Set up with TS=%d TS Idle=%d driver=%d\n", timestep, timestep_idle, driver_pid);
    }
    ++maint_counter;
    if(maint_counter==MAINT_PERIOD) {
        maint_counter=1;
    } else return;
}

static long long int filter_time(long long int nanos, struct tiacc* acc) {
    maint();
    long long int delta = nanos - acc->lastsysval;
    acc->lastsysval = nanos;
    // printf("Delta %d", delta);
    delta = time_is_frozen ? timestep_idle : delta;
    if (pending_step) {
        // printf("Dec pending step %d\n", pending_step);
        if (delta + pending_step > max_intercall_delta) {
            pending_step -= max_intercall_delta - delta;
            delta = max_intercall_delta;
        } else {
            delta += pending_step;
            pending_step = 0;
            // printf("Finished pend\n");
            if (driver_pid > 0) {
                kill(driver_pid, SIGUSR1);
            }
        }
    }
    // printf(" postscale %d\n", delta);
    acc->lastourval+=delta;
    return acc->lastourval;
}


int gettimeofday(struct timeval *tv, void *tz) {
    if(!orig_gettimeofday) {
        orig_gettimeofday = dlsym(RTLD_NEXT, "gettimeofday");
        (*orig_gettimeofday)(&timebase_gettimeofday, NULL);
    }
    int ret = orig_gettimeofday(tv, tz);
    
    long long q = 1000000LL * (tv->tv_sec - timebase_gettimeofday.tv_sec)
        + (tv->tv_usec - timebase_gettimeofday.tv_usec);

    q = filter_time(q*1000LL, accumulators+1)/1000;
    // printf("Hello! timeshift is %d/%d\n", num, denom);

    tv->tv_sec = (q/1000000)+timebase_gettimeofday.tv_sec + shift;
    tv->tv_usec = q%1000000+timebase_gettimeofday.tv_usec;
    if (tv->tv_usec >= 1000000) {
        tv->tv_usec-=1000000;
        tv->tv_sec+=1;
    }

    return ret;
}

