timestep.dylib: timestep.c Makefile
	gcc -dynamiclib -o timestep.dylib  timestep.c
